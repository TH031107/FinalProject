"""LangGraph 기반 조회/생성. 계산은 로컬, 구조화 분류·생성·검수는 모델이 담당한다."""
import asyncio
from dataclasses import dataclass, field
import re
import time
import unicodedata
import hashlib
from contextlib import asynccontextmanager
from typing import Literal, TypedDict

from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .analysis import Analysis
from .config import ModelPolicy, RetryPolicy


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Task(StrictModel):
    action: Literal['statistics','weakest','student','rank_goal','generate','greeting','unknown','refuse']
    student_alias: str | None
    question_number: int | None
    complex: bool
    target_rank: int | None = None


class Plan(StrictModel):
    tasks: list[Task] = Field(min_length=1,max_length=6)


class Exercise(StrictModel):
    passage: str = Field(min_length=1,max_length=6000)
    question: str = Field(min_length=1,max_length=1000)
    choices: list[str] = Field(min_length=5,max_length=5)
    answer: int = Field(ge=1,le=5)
    explanation: str = Field(min_length=1,max_length=3000)


class Review(StrictModel):
    approved: bool
    reason: str


from .prompts import COMMON, STAGES
from .images import source_images, render_images
from .lookup import lookup


def masked(text, aliases):
    text = unicodedata.normalize('NFKC',text)
    numeric = {}
    for real,alias in aliases.items():
        match = re.fullmatch(r'(\d+)\s*(?:번\s*)?학생',unicodedata.normalize('NFKC',real))
        if match:
            numeric.setdefault(match[1],[]).append(alias)
    def replace_number(match):
        matches = numeric.get(match[1],[])
        return matches[0] if len(matches)==1 else '[중복 학생번호]' if matches else match[0]
    text = re.sub(r'(?<!\d)(\d+)\s*(?:번\s*)?학생',replace_number,text)
    for real, alias in sorted(aliases.items(),key=lambda pair:-len(pair[0])):
        text = text.replace(real,alias)
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b','[이메일]',text)
    return re.sub(r'(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)','[전화번호]',text)


def blocked(text):
    return bool(re.search(r'시스템\s*(프롬프트|메시지)|내부\s*도구|연결된\s*도구|api\s*키|비밀\s*키|ignore.{0,30}instructions|system\s*prompt',text,re.I))


class OpenAIBackend:
    def __init__(self,key,policy=None):
        self._key=key
        self.policy=policy or ModelPolicy()
        self._client=None
        self.last_usage={}

    def selected_model(self, complex=False):
        return self.policy.complex if complex else self.policy.primary

    @asynccontextmanager
    async def request_session(self):
        from openai import AsyncOpenAI
        if self._client is not None:
            raise RuntimeError('모델 연결은 요청별로 분리해야 합니다.')
        async with AsyncOpenAI(api_key=self._key,max_retries=0,timeout=self.policy.deadline_seconds) as client:
            self._client=client
            try:
                yield
            finally:
                self._client=None

    async def complete(self,kind,payload,schema,*,complex=False,images=()):
        import json
        if self._client is None:
            async with self.request_session():
                return await self.complete(kind,payload,schema,complex=complex,images=images)
        content=[{'type':'input_text','text':json.dumps(payload,ensure_ascii=False)}]
        content += [{'type':'input_image','image_url':im} for im in images]
        self.last_usage={}
        # 일반 생성만 추론 예산을 줄인다. 복잡한 생성과 독립 검수는 medium을 유지한다.
        effort='low' if kind=='route' else (
            self.policy.generation_effort if kind=='generate' and not complex else 'medium')
        response=await self._client.responses.parse(
            model=self.selected_model(complex),instructions=COMMON+'\n'+STAGES[kind],
            input=[{'role':'user','content':content}],text_format=schema,
            reasoning={'effort':effort},
            max_output_tokens=3500 if kind=='generate' else 1200,store=False)
        usage=response.usage
        self.last_usage={k:getattr(usage,k,None) for k in ('input_tokens','output_tokens','total_tokens')} if usage else {}
        if usage:
            details=getattr(usage,'output_tokens_details',None)
            self.last_usage['reasoning_tokens']=getattr(details,'reasoning_tokens',None)
        if response.output_parsed is None:
            raise ValueError('모델 구조화 응답 없음')
        return schema.model_validate(response.output_parsed)


class PrivacyMiddleware:
    """모든 모델 단계의 텍스트 payload를 한 경로에서 전처리한다. 이미지 픽셀은 별도다."""
    def __init__(self,aliases):
        self.aliases=aliases

    def transform(self,value):
        if isinstance(value,BaseModel):
            value=value.model_dump()
        if isinstance(value,str):
            return masked(value,self.aliases)
        if isinstance(value,dict):
            return {k:self.transform(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):
            return [self.transform(v) for v in value]
        return value


@dataclass(frozen=True)
class StepRecord:
    stage: str
    seconds: float
    status: str
    model: str | None = None
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    action: str
    text: str = field(repr=False)
    status: str = 'ok'


class ChatState(TypedDict, total=False):
    sanitized_question: str
    history: list[dict]
    plan: Plan
    task_index: int
    task_results: list[TaskResult]
    pending: TaskResult | None
    source: dict | None
    draft: Exercise | None
    review: Review | None
    selected_model: str | None
    attempt_count: int
    feedback: str | None
    retryable: bool
    retry_allowed: bool
    retry_decision: str
    retry_required_seconds: float
    deadline_at: float
    fatal: bool
    answer: str


@dataclass(frozen=True)
class ChatReply:
    text: str = field(repr=False)
    elapsed: float
    status: str
    steps: tuple[StepRecord,...] = ()
    model_calls: int = 0
    active_student: str | None = None


async def ask(bundle: Analysis,question: str,history,backend,*,source_pdf=None,deadline=18.0,
              image_cache=None,active_student=None,retry_policy: RetryPolicy | None = None) -> ChatReply:
    started=time.perf_counter()
    if not 0<deadline<20:
        raise ValueError('내부 한도는20초 미만이어야 합니다.')
    if not question.strip() or len(question)>3000:
        return ChatReply('질문은1~3,000자로 입력해 주세요.',time.perf_counter()-started,'invalid')
    if blocked(question):
        return ChatReply('내부 설정·도구·비밀 정보는 공개할 수 없습니다. 성적 분석이나 문제 생성에 관해 질문해 주세요.',time.perf_counter()-started,'refused')
    aliases={s.identifier:f'S{i:04d}' for i,s in enumerate(bundle.grades.students,1)}
    reverse={v:k for k,v in aliases.items()}
    privacy=PrivacyMiddleware(aliases)
    cache=image_cache if image_cache is not None else {}
    records=[]
    progress=[]
    images=()
    identity=active_student if active_student in reverse else None
    expires=started+deadline
    retry_policy=retry_policy or RetryPolicy()

    async def call(kind,payload,schema,*,complex=False,images=()):
        model=backend.selected_model(complex) if hasattr(backend,'selected_model') else 'test_backend'
        before=time.perf_counter();status='error'
        try:
            result=await backend.complete(kind,privacy.transform(payload),schema,complex=complex,images=images)
            validated=schema.model_validate(result.model_dump() if isinstance(result,BaseModel) else result)
            status='ok'
            return validated
        except asyncio.CancelledError:
            status='timeout';raise
        finally:
            records.append(StepRecord('model_'+kind,time.perf_counter()-before,status,model,
                                      dict(getattr(backend,'last_usage',{}))))

    def current(state):
        return state['plan'].tasks[state['task_index']]

    def failed(action,text):
        return {'pending':TaskResult(action,text,'error')}

    def node(name,func):
        async def wrapped(state):
            before=time.perf_counter();status='ok'
            try:
                result=await func(state)
                pending=result.get('pending')
                if pending and pending.status!='ok':status=pending.status
                if name=='retry_gate':status=result['retry_decision']
                return result
            except asyncio.CancelledError:
                status='timeout';raise
            except ValidationError:
                status='error'
                if name=='generate':
                    feedback='필수 항목·선택지 수·정답 번호의 형식과 범위를 지정 스키마에 맞게 수정하세요.'
                    return {**failed('generate','생성 문제의 형식 검증을 통과하지 못했습니다.'),
                            'retryable':True,'feedback':feedback,'draft':None,
                            'attempt_count':state.get('attempt_count',0)+1}
                if name in {'prepare','route'}:
                    return {'fatal':True,'answer':'모델 연결 또는 응답 검증에 실패했습니다. API 키·모델 접근 권한·사용 한도를 확인해 주세요.'}
                return failed(current(state).action,'검수 응답을 확인하지 못했습니다. 다시 요청해 주세요.')
            except Exception:
                status='error'
                if name in {'prepare','route'}:
                    return {'fatal':True,'answer':'모델 연결 또는 응답 검증에 실패했습니다. API 키·모델 접근 권한·사용 한도를 확인해 주세요.'}
                return {**failed(current(state).action,'이 작업을 완료하지 못했습니다. 연결 또는 원문 근거를 확인해 다시 요청해 주세요.'),
                        'retryable':False,
                        **({'attempt_count':state.get('attempt_count',0)+1} if name=='generate' else {})}
            finally:
                records.append(StepRecord(name,time.perf_counter()-before,status))
        return wrapped

    async def prepare(state):
        return {'sanitized_question':privacy.transform(question),
                'history':[{'role':m['role'],'content':privacy.transform(m['content'][:4000])} for m in history[-12:]
                           if m.get('role') in {'user','assistant'} and isinstance(m.get('content'),str)],
                'task_index':0,'task_results':[],'pending':None,'fatal':False,'deadline_at':expires}

    async def route(state):
        nonlocal identity
        text=state['sanitized_question']
        mentioned=set(re.findall(r'(?<!\w)S\d{4}(?!\d)',text)) & set(reverse)
        unresolved=bool(re.search(r'\d+\s*(?:번\s*)?학생|\[중복 학생번호\]',text))
        for message in state['history']+[{'role':'user','content':text}]:
            if message['role']=='user':
                if re.search(r'\d+\s*(?:번\s*)?학생|\[중복 학생번호\]',message['content']):
                    identity=None
                matches=re.findall(r'(?:나는|저는|난|내가)\s*(S\d{4})(?!\d)',message['content'])
                if matches and matches[-1] in reverse:identity=matches[-1]
        if unresolved:identity=None
        plan=await call('route',{'question':text,'history':state['history'], 'available_students':list(reverse),
                        'explicit_students':sorted(mentioned),'self_identified_student':identity},Plan)
        tasks=[]
        for task in plan.tasks:
            if task.action in {'student','rank_goal'}:
                if unresolved and not mentioned:task=task.model_copy(update={'student_alias':None})
                elif len(mentioned)==1:task=task.model_copy(update={'student_alias':next(iter(mentioned))})
                elif not mentioned and identity and task.student_alias is None:task=task.model_copy(update={'student_alias':identity})
            tasks.append(task)
        return {'plan':Plan(tasks=tasks)}

    async def dispatch(state):
        task=current(state)
        model=backend.selected_model(task.complex) if hasattr(backend,'selected_model') else 'test_backend'
        return {'pending':None,'source':None,'draft':None,'review':None,'attempt_count':0,'selected_model':model,
                'feedback':None,'retryable':False,'retry_allowed':False,'retry_decision':'not_needed',
                'retry_required_seconds':0.0}

    async def retrieve(state):
        task=current(state)
        return {'pending':TaskResult(task.action,lookup(task,bundle.grades,reverse))}

    async def source(state):
        nonlocal images
        images=()
        task=current(state)
        if any(r.action=='generate' for r in state['task_results']):
            return failed('generate','한 번의 질문에서는 유사 문제를 하나만 생성합니다.')
        n=task.question_number or (bundle.grades.top_wrong[0] if bundle.grades.top_wrong else None)
        q=next((q for q in bundle.exam.questions if q.number==n),None)
        if q is None:return failed('generate','생성 기준 문항을 찾지 못했습니다. 원문 문항 번호를 지정해 주세요.')
        if 'audio_required' in q.quality_flags:return failed('generate',f'{n}번은 듣기 음원·대본이 필요해 현재 자료만으로 유사 문제를 생성할 수 없습니다.')
        if len(q.context)>16000:return failed('generate','해당 문항의 원문 범위가 커서 추가 확인이 필요합니다.')
        if set(q.quality_flags)&{'visual_required','underline_required','table_required'}:
            if source_pdf is None:return failed('generate','그림·표·밑줄을 확인할 원본 PDF가 필요합니다.')
            key=(bundle.key,hashlib.sha256(source_pdf).hexdigest(),q.pages,110)
            if key in cache:
                images=cache[key];records.append(StepRecord('image_cache_hit',0,'ok'))
            else:
                images=await render_images(source_pdf,q.pages)
                if time.perf_counter()>=expires:return failed('generate','원문 이미지 준비 중 시간 한도에 도달했습니다.')
                if len(cache)>=4:cache.pop(next(iter(cache)))
                cache[key]=images
        return {'source':{'number':n,'context':q.context,'pages':q.pages}}

    async def generate(state):
        payload={'source':state['source']}
        if state['attempt_count']:
            payload.update({'feedback':state['feedback'],'previous_draft':state.get('draft')})
        draft=await call('generate',payload,Exercise,complex=current(state).complex,images=images)
        return {'draft':draft,'attempt_count':state['attempt_count']+1,'retryable':False}

    async def validate(state):
        choices=state['draft'].choices
        if len(set(c.strip().casefold() for c in choices))!=5 or any(not c.strip() for c in choices):
            return {**failed('generate','생성 문제의 선택지 검증을 통과하지 못했습니다. 다시 요청해 주세요.'),
                    'retryable':True,'feedback':'선택지5개를 비어 있지 않고 서로 다른 내용으로 수정하고 정답·해설도 맞추세요.'}
        return {}

    async def review(state):
        result=await call('review',{'source':state['source'],'exercise':state['draft']},Review,
                          complex=current(state).complex,images=images)
        if not result.approved:
            return {'review':result,**failed('generate','생성 문제의 정답·해설 검수를 통과하지 못했습니다. 다시 요청해 주세요.'),
                    'retryable':True,'feedback':result.reason[:2000] or '정답의 유일성과 해설의 타당성을 다시 확인하고 수정하세요.'}
        exercise=state['draft'];src=state['source']
        text=f'기준 문항: {src["number"]}번 (원문 {", ".join(map(str,src["pages"]))}쪽)\n\n{exercise.passage}\n\n{exercise.question}\n'
        text+='\n'.join(f'{i}. {c}' for i,c in enumerate(exercise.choices,1))+f'\n\n정답: {exercise.answer}\n해설: {exercise.explanation}'
        return {'review':result,'pending':TaskResult('generate',text),'retryable':False}

    async def retry_gate(state):
        generated=[r.seconds for r in records if r.stage=='model_generate']
        reviewed=[r.seconds for r in records if r.stage=='model_review']
        required=retry_policy.required_seconds(generated[-1] if generated else 0,
                                              reviewed[-1] if reviewed else None)
        remaining=max(0,state['deadline_at']-time.perf_counter())
        if not state.get('retryable'):decision='not_retryable'
        elif state['attempt_count']>=retry_policy.max_attempts:decision='attempt_limit'
        elif remaining<required:decision='insufficient_time'
        else:decision='retry'
        update={'retry_allowed':decision=='retry','retry_decision':decision,'retry_required_seconds':required}
        if decision in {'attempt_limit','insufficient_time'}:
            text=state['pending'].text
            text+=' 수정 시도 횟수 한도에 도달했습니다.' if decision=='attempt_limit' else ' 수정·재검수를 완료할 시간 여유가 부족해 재생성을 생략했습니다.'
            update['pending']=TaskResult('generate',text,'error')
        return update

    async def revise(state):
        return {'pending':None,'review':None,'retryable':False,'retry_allowed':False}

    async def advance(state):
        pending=state['pending'] or TaskResult(current(state).action,'작업 결과가 없습니다.','error')
        progress.append(pending)
        return {'task_results':list(progress),'task_index':state['task_index']+1}

    async def finish(state):
        return {'answer':state.get('answer') if state.get('fatal') else '\n\n'.join(r.text for r in state['task_results'])}

    workflow=StateGraph(ChatState)
    for name,func in [('prepare',prepare),('route',route),('dispatch',dispatch),('lookup',retrieve),
                      ('source',source),('generate',generate),('validate',validate),('review',review),
                      ('retry_gate',retry_gate),('revise',revise),
                      ('advance',advance),('finish',finish)]:
        workflow.add_node(name,node(name,func))
    workflow.add_edge(START,'prepare')
    workflow.add_conditional_edges('prepare',lambda s:'finish' if s.get('fatal') else 'route', ['finish','route'])
    workflow.add_conditional_edges('route',lambda s:'finish' if s.get('fatal') else 'dispatch', ['finish','dispatch'])
    workflow.add_conditional_edges('dispatch',lambda s:'source' if current(s).action=='generate' else 'lookup',['source','lookup'])
    workflow.add_edge('lookup','advance')
    workflow.add_conditional_edges('source',lambda s:'advance' if s.get('pending') else 'generate',['advance','generate'])
    for name,next_node in [('generate','validate'),('validate','review')]:
        workflow.add_conditional_edges(name,lambda s,n=next_node:'retry_gate' if s.get('pending') else n,['retry_gate',next_node])
    workflow.add_conditional_edges('review',lambda s:'retry_gate' if s.get('pending') and s['pending'].status!='ok' else 'advance',['retry_gate','advance'])
    workflow.add_conditional_edges('retry_gate',lambda s:'revise' if s['retry_allowed'] else 'advance',['revise','advance'])
    workflow.add_edge('revise','generate')
    workflow.add_conditional_edges('advance',lambda s:'dispatch' if s['task_index']<len(s['plan'].tasks) else 'finish',['dispatch','finish'])
    workflow.add_edge('finish',END)

    @asynccontextmanager
    async def connection():
        if hasattr(backend,'request_session'):
            async with backend.request_session():yield
        else:yield

    async def run():
        async with connection():
            with tracing_context(enabled=False):
                return await workflow.compile().ainvoke({},config={'recursion_limit':70})

    status='error';text=''
    try:
        output=await asyncio.wait_for(run(),timeout=max(.001,expires-time.perf_counter()))
        text=output['answer']
        status='error' if output.get('fatal') or all(r.status!='ok' for r in progress) else 'partial' if any(r.status!='ok' for r in progress) else 'ok'
    except asyncio.TimeoutError:
        status='timeout'
        text='\n\n'.join(r.text for r in progress)
        text+=('\n\n' if text else '')+'응답 시간 한도에 도달했습니다. 미완료 작업은 질문 범위를 줄여 다시 요청해 주세요.'
    except Exception:
        text='\n\n'.join(r.text for r in progress)
        text+=('\n\n' if text else '')+'모델 연결 또는 응답 검증에 실패했습니다. API 키·모델 접근 권한·사용 한도를 확인해 주세요.'
    return ChatReply(text,time.perf_counter()-started,status,tuple(records),
                     sum(r.model is not None for r in records),identity)
