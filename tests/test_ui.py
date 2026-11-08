"""AppTest는 페이지/버튼을 실행한다. 업로더 반환값만 합성 파일로 주입한다."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import streamlit as st
from streamlit.testing.v1 import AppTest
from academy.analysis import analyze
from academy.session import synchronize
from test_parsing import answer_pdf,question_pdf,omr

ENTRY=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class UITests(unittest.TestCase):
    def setUp(self):
        self.files={}
        original=st.file_uploader
        def upload(label,*args,**kwargs):
            original(label,*args,**kwargs)
            if not kwargs['key'].endswith('_0'):
                return None
            value=self.files.get(label)
            return SimpleNamespace(name=value[0],getvalue=lambda:value[1]) if value else None
        self.patcher=patch('streamlit.file_uploader',side_effect=upload)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def valid_files(self):
        self.files.update({'문제 PDF':('q.pdf',question_pdf()),
            '정답 PDF':('a.pdf',answer_pdf([['1','1','2','3','3','4'],['2','2','3','','','']])),
            'OMR 엑셀':('s.xlsx',omr())})

    def app(self):
        app=AppTest.from_file(str(ENTRY),default_timeout=20).run()
        self.assertFalse(app.exception)
        app.switch_page('app_pages/results.py').run()
        self.assertFalse(app.exception)
        return app

    def test_pages_and_empty_state(self):
        at=self.app()
        at.switch_page('app_pages/home.py').run()
        self.assertEqual(at.title[0].value,'학원 평가 도우미')
        self.assertFalse(any(b.key=='analyze' for b in at.button))
        at.switch_page('app_pages/results.py').run()
        self.assertTrue(at.button(key='analyze').disabled)
        self.assertFalse(at.exception)
        self.assertTrue(at.get('download_button')[0].proto.disabled)
        self.assertTrue(at.chat_input[0].disabled)

    def test_analyze_download_and_reuse_across_navigation(self):
        self.valid_files()
        with patch('academy.analysis.analyze',wraps=analyze) as call:
            at=self.app()
            at.button(key='analyze').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.title[0].value,'업로드 및 분석')
            self.assertFalse(at.get('download_button')[0].proto.disabled)
            self.assertEqual(at.metric[0].value,'1')
            self.assertEqual(at.metric[1].value,'6.00')
            at.switch_page('app_pages/home.py').run()
            at.switch_page('app_pages/results.py').run()
            at.button(key='analyze').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(call.call_count,1)

    def test_intro_only_links_to_analysis_and_analysis_never_forces_page_switch(self):
        self.valid_files()
        at=AppTest.from_file(str(ENTRY),default_timeout=20).run()
        self.assertFalse(at.exception)
        self.assertFalse(any(b.key=='analyze' for b in at.button))
        self.assertNotIn('analysis',at.session_state)
        at.switch_page('app_pages/results.py').run()
        with patch('streamlit.switch_page',side_effect=AssertionError('unexpected programmatic switch')) as switch:
            at.button(key='analyze').click().run()
            self.assertFalse(at.exception)
            self.assertFalse(at.get('download_button')[0].proto.disabled)
            self.assertEqual(at.metric[0].value,'1')
            switch.assert_not_called()

    def test_file_change_and_removal_invalidate_results_and_history(self):
        self.valid_files();at=self.app()
        at.button(key='analyze').click().run()
        at.session_state['messages']=['synthetic history']
        self.files['OMR 엑셀']=('s.xlsx',omr([['synthetic-B',2,2,3]]))
        at.switch_page('app_pages/results.py').run()
        self.assertIsNone(at.session_state['analysis'] if 'analysis' in at.session_state else None)
        self.assertEqual(at.session_state.get('messages'),[])
        self.assertTrue(at.get('download_button')[0].proto.disabled)
        at.button(key='analyze').click().run()
        self.assertFalse(at.exception)
        self.files.pop('정답 PDF');at.switch_page('app_pages/results.py').run()
        self.assertTrue(at.button(key='analyze').disabled)
        self.assertTrue(at.get('download_button')[0].proto.disabled)

    def test_column_change_and_clear(self):
        self.valid_files();at=self.app()
        at.button(key='analyze').click().run()
        at.selectbox(key='question_columns').select(1).run()
        self.assertNotIn('analysis',at.session_state)
        at.button(key='clear').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['upload_epoch'],1)
        self.assertTrue(at.button(key='analyze').disabled)

    def test_parse_failure_does_not_leave_download_or_private_error(self):
        self.valid_files();at=self.app()
        at.button(key='analyze').click().run()
        self.files['문제 PDF']=('private-filename.pdf',b'%PDF-broken')
        at.switch_page('app_pages/results.py').run();at.button(key='analyze').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(at.error)
        self.assertNotIn('private-filename',at.error[0].value)
        at.switch_page('app_pages/results.py').run()
        self.assertTrue(at.get('download_button')[0].proto.disabled)

    def test_sessions_do_not_share_analysis(self):
        self.valid_files();a=self.app()
        a.button(key='analyze').click().run()
        b=self.app()
        self.assertNotIn('analysis',b.session_state)
        self.assertIn('analysis',a.session_state)

    def test_session_policy_clears_all_derived_state(self):
        state={'input_key':'old','analysis':object(),'messages':['x'],'agent_state':object(),'active_student':'x'}
        synchronize(state,'new')
        self.assertEqual(state,{'input_key':'new'})
        state['analysis']='same'
        synchronize(state,'new')
        self.assertEqual(state['analysis'],'same')

    def test_chat_key_gating_and_history_passed_to_backend(self):
        from unittest.mock import AsyncMock
        from academy.chat import ChatReply
        self.valid_files();at=self.app()
        at.button(key='analyze').click().run()
        self.assertTrue(at.chat_input[0].disabled)
        at.text_input(key='runtime_api_key').set_value('synthetic-test-key').run()
        self.assertFalse(at.chat_input[0].disabled)
        handler=AsyncMock(return_value=ChatReply('합성 테스트 응답',.01,'ok'))
        with patch('academy.chat.ask',handler):
            at.chat_input[0].set_value('평균은?').run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.session_state['messages']),2)
            at.chat_input[0].set_value('중앙값도?').run()
            self.assertFalse(at.exception)
            self.assertEqual(len(handler.call_args.args[2]),2)
            self.assertEqual(at.session_state['messages'][0]['content'],'평균은?')


if __name__=='__main__':
    unittest.main()
