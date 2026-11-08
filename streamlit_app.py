"""실행: python -m streamlit run streamlit_app.py"""
import streamlit as st

from academy.analysis import analysis_key, analyze
from academy.inputs import InputRole, InputValidationError, Upload, validate_uploads
from academy.session import reset_analysis, synchronize

st.set_page_config(page_title='학원 평가 도우미', page_icon=':material/school:', layout='wide')
page = st.navigation([
    st.Page('app_pages/home.py', title='서비스 소개', icon=':material/home:', default=True),
    st.Page('app_pages/results.py', title='업로드 및 분석', icon=':material/analytics:'),
])

with st.sidebar:
    st.header('시험 자료')
    st.caption('문제지·정답지·OMR을 올린 뒤 분석을 시작하세요. 파일당 최대 20MB입니다.')
    epoch = st.session_state.setdefault('upload_epoch', 0)
    uploads = {}
    for role,label,types in [(InputRole.QUESTION,'문제 PDF',['pdf']),
                             (InputRole.ANSWER,'정답 PDF',['pdf']),
                             (InputRole.OMR,'OMR 엑셀',['xlsx'])]:
        item = st.file_uploader(label, type=types, key=f'{role.value}_{epoch}', max_upload_size=20)
        if item is not None:
            uploads[role] = Upload(item.name,item.getvalue())
    columns = st.selectbox('문제지 단 수', [2,1], key='question_columns',
                           help='제공 문제지는 두 단입니다. 한 단 문제지는 1을 선택하세요.')
    ready, error = None, None
    try:
        if uploads:
            ready = validate_uploads(uploads)
    except InputValidationError as exc:
        error = str(exc)
    synchronize(st.session_state, analysis_key(ready,columns) if ready is not None else None)
    if error:
        st.warning(error)
    on_results = page.url_path == 'results'
    if not on_results:
        st.info('분석은 업로드 및 분석 페이지에서 시작해 주세요.')
        st.page_link('app_pages/results.py', label='업로드 및 분석으로 이동', icon=':material/analytics:')
    if on_results and st.button('분석 시작', key='analyze', type='primary', disabled=ready is None, width='stretch'):
        if st.session_state.get('analysis') is None:
            reset_analysis(st.session_state)
            try:
                with st.spinner('문항과 응답을 읽고 채점하는 중입니다…'):
                    st.session_state['analysis'] = analyze(ready, question_columns=columns)
            except InputValidationError as exc:
                st.error(str(exc))
            except Exception:
                st.error('분석을 완료하지 못했습니다. 파일 형식과 문제지 단 수를 확인해 주세요.')
    if st.button('자료와 결과 지우기', key='clear', width='stretch'):
        reset_analysis(st.session_state)
        st.session_state['input_key'] = None
        st.session_state['upload_epoch'] += 1
        st.rerun()
    st.caption('자료는 현재 세션에서 처리합니다. 분석 데이터는 전역 캐시나 서버 파일로 저장하지 않습니다.')

page.run()
