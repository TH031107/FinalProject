import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from academy.chat import ask, OpenAIBackend, Exercise, Review
from academy.images import render_images
from academy.session import synchronize
import test_chat as chat_fixtures
from test_chat import FakeBackend, task
from test_parsing import question_pdf


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def bundle(self, visual=False):
        b=chat_fixtures.ChatTests().bundle()
        if visual:
            qs=list(b.exam.questions);qs[2]=replace(qs[2],quality_flags=('underline_required',))
            b=replace(b,exam=replace(b.exam,questions=tuple(qs)))
        return b

    async def test_failed_generation_preserves_statistics_and_marks_partial(self):
        class Fails(FakeBackend):
            async def complete(self,kind,payload,schema,**kwargs):
                if kind=='generate': raise RuntimeError('synthetic sensitive error')
                return await super().complete(kind,payload,schema,**kwargs)
        reply=await ask(self.bundle(),'synthetic',[],Fails([task('statistics'),task('generate'),task('greeting')]))
        self.assertEqual(reply.status,'partial')
        self.assertIn('3.00',reply.text);self.assertIn('안녕하세요',reply.text)
        self.assertNotIn('sensitive error',reply.text)
        self.assertTrue(any(r.stage=='generate' and r.status=='error' for r in reply.steps))

    async def test_actual_graph_has_explicit_nodes_and_conditional_branches(self):
        from langgraph.graph import StateGraph
        original=StateGraph.compile;graphs=[]
        def capture(self,*args,**kwargs):
            graph=original(self,*args,**kwargs);graphs.append(graph.get_graph());return graph
        with patch.object(StateGraph,'compile',capture):
            reply=await ask(self.bundle(),'synthetic',[],FakeBackend([task('statistics')]))
        self.assertEqual(reply.model_calls,1)
        graph=graphs[0]
        self.assertTrue({'prepare','route','dispatch','lookup','source','generate','validate','review','advance','finish'}.issubset(graph.nodes))
        self.assertTrue(any(e.source=='dispatch' and e.target=='source' and e.conditional for e in graph.edges))
        self.assertFalse(any(r.stage=='generate' for r in reply.steps))

    async def test_request_client_is_shared_and_closed_with_same_call_count(self):
        import httpx,openai
        real_client=openai.AsyncOpenAI;requests=[];clients=[]
        def response(req):
            body=json.loads(req.content);requests.append(body)
            name=body['text']['format']['name']
            t=task('generate',complex=True)
            value={'tasks':[t]} if name=='Plan' else {'approved':True,'reason':'ok'} if name=='Review' else {
                'passage':'Synthetic passage','question':'Title?', 'choices':['one','two','three','four','five'],
                'answer':1,'explanation':'Synthetic explanation'}
            return httpx.Response(200,json={'id':'resp_synthetic','object':'response','created_at':0,
                'model':body['model'],'status':'completed','usage':{'input_tokens':10,'output_tokens':5,'total_tokens':15},
                'output':[{'id':'msg_synthetic','type':'message','role':'assistant','status':'completed',
                'content':[{'type':'output_text','annotations':[],'text':json.dumps(value)}]}]})
        def client(**kwargs):
            c=real_client(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)));clients.append(c);return c
        backend=OpenAIBackend('synthetic-test-key')
        with patch('openai.AsyncOpenAI',side_effect=client):
            reply=await ask(self.bundle(),'synthetic',[],backend)
        self.assertEqual(reply.status,'ok');self.assertEqual(reply.model_calls,3)
        self.assertEqual(len(clients),1);self.assertTrue(clients[0].is_closed())
        self.assertIsNone(backend._client)
        self.assertEqual([r['model'] for r in requests],['gpt-6.1-sol','gpt-6-astra','gpt-6-astra'])
        self.assertNotIn('원문 복사',requests[0]['instructions'])
        self.assertFalse(any('synthetic-test-key' in str(r) for r in reply.steps))

    async def test_common_middleware_masks_generated_text_before_review(self):
        class Sensitive(FakeBackend):
            async def complete(self,kind,payload,schema,**kwargs):
                r=await super().complete(kind,payload,schema,**kwargs)
                return r.model_copy(update={'passage':'user@example.com 010-1234-5678'}) if kind=='generate' else r
        b=Sensitive([task('generate')]);await ask(self.bundle(),'synthetic',[],b)
        review=b.calls[-1][1]['exercise']['passage']
        self.assertNotIn('user@example.com',review);self.assertNotIn('010-1234',review)

    async def test_image_cache_reuses_only_matching_data_and_is_bounded(self):
        cache={};renderer=AsyncMock(return_value=('data:image/png;base64,synthetic',))
        with patch('academy.chat.render_images',renderer):
            for key in ['first','first','new','third','fourth','fifth']:
                reply=await ask(replace(self.bundle(True),key=key),'synthetic',[],FakeBackend([task('generate')]),
                                source_pdf=b'synthetic',image_cache=cache)
                self.assertEqual(reply.status,'ok')
            self.assertEqual(renderer.await_count,5)
            self.assertEqual(len(cache),4)
        state={'input_key':'before','image_cache':cache,'active_student':'S0001','chat_diagnostics':{'x':1}}
        synchronize(state,'after')
        self.assertEqual(state,{'input_key':'after'})

    async def test_cache_hit_does_not_add_model_calls(self):
        cache={};renderer=AsyncMock(return_value=('data:image/png;base64,synthetic',))
        with patch('academy.chat.render_images',renderer):
            await ask(self.bundle(True),'synthetic',[],FakeBackend([task('generate')]),source_pdf=b'x',image_cache=cache)
            reply=await ask(self.bundle(True),'synthetic',[],FakeBackend([task('generate')]),source_pdf=b'x',image_cache=cache)
        self.assertEqual(reply.model_calls,3)
        self.assertTrue(any(r.stage=='image_cache_hit' for r in reply.steps))

    async def test_image_process_is_killed_and_waited_on_timeout(self):
        class Worker:
            returncode=None
            async def communicate(self,payload):await asyncio.sleep(3)
            def kill(self):self.returncode=-9
            async def wait(self):return self.returncode
        worker=Worker()
        with patch('academy.images.asyncio.create_subprocess_exec',AsyncMock(return_value=worker)):
            before=time.perf_counter()
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(render_images(b'x',(1,)),timeout=.1)
        self.assertEqual(worker.returncode,-9)
        self.assertLess(time.perf_counter()-before,1)

    async def test_real_worker_renders_without_temp_files(self):
        images=await render_images(question_pdf(),(1,))
        self.assertTrue(images[0].startswith('data:image/png;base64,iVBOR'))

    async def test_identity_survives_bounded_history_as_explicit_session_alias(self):
        b=chat_fixtures.ChatTests().numbered_bundle()
        backend=FakeBackend([task('student')])
        reply=await ask(b,'성적은?',[],backend,active_student='S0001')
        self.assertIn('731학생: 5점',reply.text)
        self.assertEqual(reply.active_student,'S0001')
        history=[{'role':'user','content':'나는 731번 학생이야'}, {'role':'user','content':'나는 9999번 학생이야'}]
        reply=await ask(b,'내 성적은?',history,FakeBackend([task('student')]),active_student=None)
        self.assertIn('확인할 수 없',reply.text)


if __name__=='__main__':unittest.main()
