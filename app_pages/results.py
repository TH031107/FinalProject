import streamlit as st
from academy.parsing import QUALITY_LABELS
import asyncio
import os
import time
from dataclasses import asdict
from academy.chat import ask, OpenAIBackend

st.title('업로드 및 분석')
bundle = st.session_state.get('analysis')
if bundle is None:
    st.info('사이드바에서 세 파일을 업로드하고 분석 시작을 눌러 주세요.')
    st.download_button('엑셀 내려받기', data=b'', file_name='exam_results.xlsx', disabled=True)
else:
    result, exam = bundle.grades, bundle.exam
    st.success('분석이 완료되었습니다.')
    with st.container(horizontal=True):
        st.metric('학생 수', len(result.students))
        st.metric('평균', f'{result.average:.2f}')
        st.metric('중앙값', f'{result.median:.2f}')
        st.metric('표준편차', f'{result.population_stddev:.2f}')
    st.caption(f'만점 {result.maximum}점 · 표준편차는 전체 업로드 학생 기준 · 미응답은 0점입니다.')
    st.download_button('엑셀 내려받기', data=bundle.excel, file_name='exam_results.xlsx',
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', on_click='ignore',
                       icon=':material/download:', key='download_results')
    st.subheader('가장 많이 틀린 문항')
    st.text(', '.join(f'{n}번' for n in result.top_wrong) if result.top_wrong else '모든 문항에 오답자가 없습니다.')
    st.caption('오답자 수가 같으면 문항 번호순으로 최대 3개를 표시합니다. 상세 동률 기준은 엑셀에서 확인할 수 있습니다.')
    students_tab, questions_tab, sources_tab = st.tabs(['학생 성적','문항 통계','원문 확인'])
    with students_tab:
        st.dataframe([{'학생명':s.identifier,'점수':s.score,'순위':s.rank,'정답 수':sum(s.correct),
                       '미응답 수':sum(s.unanswered)} for s in result.students], hide_index=True, alt='학생별 성적과 순위')
    with questions_tab:
        st.dataframe([{'문항':q.number,'배점':q.points,'오답자 수':q.wrong_count,'미응답 수':q.unanswered_count,
                       '오답률(%)':round(q.wrong_rate*100,2)} for q in result.questions], hide_index=True, alt='문항별 배점과 오답률')
    with sources_tab:
        st.dataframe([{'문항':q.number,'원문 페이지':', '.join(map(str,q.pages)),
                       '확인할 내용':', '.join(QUALITY_LABELS.get(f,f) for f in q.quality_flags)} for q in exam.questions],
                      hide_index=True, alt='문항별 원문 페이지와 추출 품질 경고')
        for warning in exam.warnings:
            st.warning(warning)
st.divider()
st.subheader('성적 대화')
with st.expander('챗봇 연결 설정'):
    def remember_key():
        st.session_state['_chat_api_key']=st.session_state.get('runtime_api_key','')
    entered_key=st.text_input('OpenAI API 키',type='password',key='runtime_api_key',
                             value=st.session_state.get('_chat_api_key',''),on_change=remember_key,
                             help='현재 세션에서만 사용합니다. 코드나 GitHub에 저장하지 마세요.')
    st.caption('질문·최근 대화와 필요한 문항 근거를 OpenAI로 전송합니다. 학생 식별자는 가명으로 바꾸고 이메일·휴대전화 패턴을 가립니다. 원문 페이지 이미지는 픽셀 마스킹을 하지 않으므로 개인정보가 있는 문제지는 사용하지 마세요.')
key=entered_key or os.environ.get('OPENAI_API_KEY','')
if not key:
    st.info('채점과 다운로드는 키 없이 사용할 수 있습니다. 챗봇을 사용하려면 연결 설정에 API 키를 입력하세요.')
messages=st.session_state.setdefault('messages',[])
for message in messages:
    with st.chat_message(message['role']):
        st.text(message['content'])
prompt=st.chat_input('성적에 관해 질문하세요',disabled=bundle is None or not key,key='chat_placeholder',max_chars=3000)
if prompt:
    with st.chat_message('user'):
        st.text(prompt)
    with st.chat_message('assistant'):
        wall_started=time.perf_counter()
        with st.spinner('질문에 답하는 중입니다…'):
            epoch=st.session_state.get('upload_epoch',0)
            uploaded_pdf=st.session_state.get(f'question_pdf_{epoch}')
            reply=asyncio.run(ask(bundle,prompt,list(messages),OpenAIBackend(key),
                                 source_pdf=uploaded_pdf.getvalue() if uploaded_pdf else None,
                                 image_cache=st.session_state.setdefault('image_cache',{}),
                                 active_student=st.session_state.get('active_student')))
        st.text(reply.text)
        if reply.status=='partial':
            st.warning('일부 작업만 완료됐습니다. 미완료 작업을 확인해 주세요.')
        elif reply.status in {'error','timeout'}:
            st.warning('요청 전체를 완료하지 못했습니다.')
        wall_elapsed=time.perf_counter()-wall_started
        st.caption(f'전체 처리 시간 {wall_elapsed:.2f}초 · 모델 호출 {reply.model_calls}회')
        st.session_state['active_student']=reply.active_student
        diagnostics={'status':reply.status,'graph_seconds':reply.elapsed,'wall_seconds':wall_elapsed,
                     'model_calls':reply.model_calls,'steps':[asdict(step) for step in reply.steps]}
        st.session_state['chat_diagnostics']=diagnostics
        with st.expander('이번 답변 처리 내역'):
            st.caption('시간 초과·일부 실패는 정상 답변 완료로 보지 않습니다. 단계 시간과 모델 호출 시간은 겹치므로 합산하지 않습니다.')
            rows=[{**step, **{name:step['usage'].get(name) for name in
                             ('input_tokens','output_tokens','reasoning_tokens')}}
                  for step in diagnostics['steps']]
            st.caption('output_tokens에는 reasoning_tokens가 포함됩니다. 취소된 호출은 사용량을 받지 못할 수 있습니다. 토큰 수만으로 지연 원인이나 정확도를 확정할 수 없습니다.')
            st.dataframe(rows,hide_index=True,alt='단계별 시간·선택 모델·처리 상태·토큰 사용량',
                         column_order=['stage','seconds','status','model','input_tokens','output_tokens','reasoning_tokens'])
    messages.extend([{'role':'user','content':prompt},{'role':'assistant','content':reply.text}])
    st.session_state['messages']=messages[-24:]
