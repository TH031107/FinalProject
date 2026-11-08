"""화면·챗봇·다운로드에서 재사용할 분석 묶음. 전역 학생 데이터 캐시는 만들지 않는다."""
from dataclasses import dataclass, field
import hashlib

from .export import export_excel
from .grading import GradeResult, grade_exam
from .inputs import ValidatedInputs
from .parsing import ParsedExam, parse_exam

PIPELINE_VERSION = 'exam-analysis-v4'


def analysis_key(inputs: ValidatedInputs, question_columns: int = 2) -> str:
    if question_columns not in (1,2):
        raise ValueError('문제지 단 수는 1 또는 2여야 합니다.')
    return hashlib.sha256(f'{PIPELINE_VERSION}:{inputs.fingerprint}:{question_columns}'.encode()).hexdigest()


@dataclass(frozen=True)
class Analysis:
    key: str
    exam: ParsedExam = field(repr=False)
    grades: GradeResult = field(repr=False)
    excel: bytes = field(repr=False)


def analyze(inputs: ValidatedInputs, *, question_columns: int = 2) -> Analysis:
    exam = parse_exam(inputs, question_columns=question_columns)
    grades = grade_exam(exam)
    # 다음 UI 단계에서 이 묶음을 세션에 저장한다. 질문마다 다시 분석하지 않는다.
    return Analysis(analysis_key(inputs,question_columns),exam,grades,export_excel(grades,exam))
