import streamlit as st

st.title('학원 평가 도우미')
st.markdown('시험 자료를 올리면 학생별 성적과 문항별 오답을 한 번에 확인할 수 있습니다.')
with st.container(border=True):
    st.subheader('세 파일로 시작하세요')
    st.markdown('1. 문제 PDF, 정답 PDF, OMR 엑셀을 올립니다.\n2. **분석 시작**을 누릅니다.\n3. 성적과 오답 현황을 확인하고 엑셀로 내려받습니다.')
    st.page_link('app_pages/results.py', label='업로드 및 분석으로 이동', icon=':material/arrow_forward:')
with st.expander('사용할 수 있는 자료 형식'):
    st.markdown('정답지는 **문항번호·정답·배점** 격자 표가 있는 PDF를 사용합니다. OMR 엑셀에는 **OMR_응답** 시트와 **학생명, Q01, Q02…** 열이 필요합니다.')
    st.caption('스캔 자료의 자동 문자 인식과 듣기 음원 분석은 지원하지 않습니다.')
st.info('자동 채점과 엑셀 다운로드는 바로 사용할 수 있습니다. 성적 대화와 유사 문제 생성은 분석 후 API 키를 연결하면 사용할 수 있습니다.')
