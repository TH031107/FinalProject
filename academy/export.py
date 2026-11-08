"""채점 결과를 다운로드용 XLSX 바이트로 만든다. 파일/대화를 서버에 저장하지 않는다."""
from io import BytesIO
from math import ceil
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .grading import GradeResult
from .parsing import ParsedExam, QUALITY_LABELS


def export_excel(result: GradeResult, exam: ParsedExam) -> bytes:
    if result.fingerprint != exam.fingerprint:
        raise ValueError('분석 결과와 원본 파일이 일치하지 않습니다.')
    workbook = Workbook()
    workbook.remove(workbook.active)

    def sheet(name, rows, widths, *, filter_rows=True):
        ws = workbook.create_sheet(name)
        for row in rows:
            ws.append(row)
            # 이름이 = 또는 + 등으로 시작해도 실행 가능한 수식으로 쓰지 않는다.
            for cell in ws[ws.max_row]:
                if isinstance(cell.value,str):
                    cell.data_type = 's'
                cell.font = Font(name='맑은 고딕',size=11)
                cell.alignment = Alignment(vertical='center',wrap_text=True)
            lines = max((ceil(sum(2 if ord(ch)>127 else 1 for ch in str(value))/max(width-2,1))
                         for value,width in zip(row,widths)),default=1)
            ws.row_dimensions[ws.max_row].height = max(26, min(150, lines*16+6))
        for cell in ws[1]:
            cell.fill = PatternFill('solid',fgColor='17365D')
            cell.font = Font(name='맑은 고딕',bold=True,color='FFFFFF')
        for index,width in enumerate(widths,1):
            ws.column_dimensions[get_column_letter(index)].width = width
        ws.freeze_panes = 'B2'
        if filter_rows:
            ws.auto_filter.ref = ws.dimensions
        return ws

    summary = sheet('성적 개요',[
        ['항목','값'],['학생 수',len(result.students)],['문항 수',len(result.questions)],['만점',result.maximum],
        ['평균',result.average],['중앙값',result.median],['표준편차(모집단)',result.population_stddev],
        ['가장 많이 틀린 문항(최대3개)', ', '.join(map(str,result.top_wrong)) or '없음'],
        ['상위3개 선정 기준','오답자 수 내림차순, 동률이면 문항 번호 오름차순'],
        ['선정 경계에서 같은 오답 수인 문항', ', '.join(map(str,result.top_boundary_ties)) or '없음'],
        ['미응답 처리','0점, 오답 수에 포함'],['표준편차 기준','업로드된 전체 학생의 모집단 표준편차(ddof=0)'],
        ['순위 기준','공동 순위 적용(예: 1, 1, 3)'],
    ],[42,85],filter_rows=False)
    for row in (5,6,7):
        summary.cell(row,2).number_format = '0.00'
    sheet('학생 성적', [['학생명','점수','순위','정답 수','오답 수(미응답 포함)','미응답 수']]+
          [[s.identifier,s.score,s.rank,sum(s.correct),len(s.correct)-sum(s.correct),sum(s.unanswered)] for s in result.students],
          [24,12,12,12,24,14])
    sheet('문항별 정답 여부', [['학생명']+[f'Q{q.number:02d}' for q in result.questions]]+
          [[s.identifier]+['미응답' if blank else 'O' if ok else 'X' for ok,blank in zip(s.correct,s.unanswered)] for s in result.students],
          [24]+[10]*len(result.questions))
    qs = sheet('문항 통계', [['문항','배점','정답자 수','오답자 수(미응답 포함)','미응답 수','오답률']]+
               [[q.number,q.points,q.correct_count,q.wrong_count,q.unanswered_count,q.wrong_rate] for q in result.questions],
               [10,10,14,26,14,14])
    for row in qs.iter_rows(min_row=2,min_col=6,max_col=6):
        row[0].number_format = '0.0%'
    sheet('원문 확인', [['문항','관련 페이지','추출 품질 표시(검토 필요)']]+
          [[q.number,', '.join(map(str,q.pages)),', '.join(QUALITY_LABELS.get(f,f) for f in q.quality_flags)] for q in exam.questions], [10,18,95])
    data = BytesIO()
    workbook.save(data)
    workbook.close()
    return data.getvalue()
