"""채점·통계의 단일 계산 경로. LLM·외부 호출·고정 정답을 사용하지 않는다."""
from dataclasses import dataclass, field
from statistics import mean, median, pstdev
import unicodedata

from .parsing import ParsedExam


@dataclass(frozen=True)
class StudentScore:
    identifier: str = field(repr=False)
    score: int
    rank: int
    correct: tuple[bool, ...] = field(repr=False)
    unanswered: tuple[bool, ...] = field(repr=False)
    earned: tuple[int, ...] = field(repr=False)


@dataclass(frozen=True)
class QuestionStats:
    number: int
    points: int
    correct_count: int
    wrong_count: int
    unanswered_count: int
    wrong_rate: float


@dataclass(frozen=True)
class GradeResult:
    fingerprint: str
    students: tuple[StudentScore, ...] = field(repr=False)
    questions: tuple[QuestionStats, ...]
    maximum: int
    average: float
    median: float
    population_stddev: float
    top_wrong: tuple[int, ...]
    top_boundary_ties: tuple[int, ...]

    def student(self, identifier: str) -> StudentScore:
        """정확한 식별자만 조회한다. 불명확한 이름을 임의 학생으로 연결하지 않는다."""
        key = unicodedata.normalize('NFC', str(identifier)).strip()
        for student in self.students:
            if student.identifier == key:
                return student
        raise LookupError('해당 학생 식별자를 찾을 수 없습니다.')

    def weakest(self) -> tuple[StudentScore, ...]:
        lowest = min(s.score for s in self.students)
        return tuple(s for s in self.students if s.score == lowest)

    def rank_goal(self, identifier: str, target_rank: int) -> dict:
        """다른 학생 점수 고정·공동 순위 허용. 고배점 오답부터 바꾼 최소 문항 수."""
        if type(target_rank) is not int or not 1 <= target_rank <= len(self.students):
            raise ValueError('목표 순위는 전체 학생 수 범위의 정수여야 합니다.')
        student = self.student(identifier)
        if student.rank <= target_rank:
            return {'already':True,'score':student.score,'rank':student.rank}
        peers = sorted((s.score for s in self.students if s.identifier != student.identifier),reverse=True)
        threshold = peers[target_rank-1]
        needed = max(0,threshold-student.score)
        wrong = sorted(((q.points,q.number) for q,ok in zip(self.questions,student.correct) if not ok),reverse=True)
        total, chosen = 0, []
        for points,number in wrong:
            if total >= needed:
                break
            total += points
            chosen.append(number)
        if total < needed:
            raise ValueError('목표 점수에 도달할 수 없습니다.')
        count = len(chosen)
        return {'already':False,'score':student.score,'rank':student.rank,'threshold':threshold,
                'needed_points':needed,'extra_count':count,'gain':total,
                'new_score':student.score+total,'percentage_points':count/len(self.questions)*100,
                'correct_rate':sum(student.correct)/len(self.questions)*100,
                'relative_increase':count/sum(student.correct)*100 if any(student.correct) else None,
                'chosen':tuple(sorted(chosen))}


def grade_exam(exam: ParsedExam) -> GradeResult:
    """미응답은 오답(0점), 감점 없음. 순위는 공동 순위 다음을 건너뛴다."""
    answers = exam.answers
    numbers = tuple(a.number for a in answers)
    if not answers or numbers != tuple(range(1,len(answers)+1)) or not exam.students:
        raise ValueError('연속된 정답표와 학생 응답이 필요합니다.')
    if tuple(q.number for q in exam.questions) != numbers:
        raise ValueError('문제와 정답 문항이 일치하지 않습니다.')
    if any(type(a.choice) is not int or a.choice not in range(1,6) or type(a.points) is not int or a.points <= 0 for a in answers):
        raise ValueError('정답·배점을 확인해 주세요.')
    identifiers = [s.identifier for s in exam.students]
    if any(not i or i != unicodedata.normalize('NFC', i).strip() for i in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError('학생 식별자는 비어 있지 않고 고유해야 합니다.')
    calculated = []
    for s in exam.students:
        if len(s.responses) != len(answers) or any(v is not None and (type(v) is not int or v not in range(1,6)) for v in s.responses):
            raise ValueError('응답 수 또는 선택지 값이 잘못되었습니다.')
        correct = tuple(v == a.choice for v,a in zip(s.responses,answers))
        earned = tuple(a.points if ok else 0 for a,ok in zip(answers,correct))
        calculated.append((s.identifier,sum(earned),correct,tuple(v is None for v in s.responses),earned))
    totals = [r[1] for r in calculated]
    # 정렬 후 첫 위치가 공동 순위다. 학생마다 전체 학생을 다시 훑지 않는다.
    ranks = {}
    for position, total in enumerate(sorted(totals,reverse=True),1):
        ranks.setdefault(total, position)
    students = tuple(StudentScore(i,total,ranks[total],c,u,e) for i,total,c,u,e in calculated)
    count = len(students)
    questions = []
    for index,a in enumerate(answers):
        correct_count = sum(s.correct[index] for s in students)
        wrong_count = count-correct_count
        questions.append(QuestionStats(a.number,a.points,correct_count,wrong_count,
                                       sum(s.unanswered[index] for s in students),wrong_count/count))
    # '틀린 문항'은 오답자가 있는 문항만. 동률이면 문항 번호 오름차순으로 정확히 최대3개.
    ordered = sorted((q for q in questions if q.wrong_count),key=lambda q:(-q.wrong_count,q.number))
    top = ordered[:3]
    boundary = tuple(q.number for q in ordered if top and q.wrong_count == top[-1].wrong_count)
    return GradeResult(exam.fingerprint,students,tuple(questions),sum(a.points for a in answers),
                       mean(totals),median(totals),pstdev(totals),tuple(q.number for q in top),boundary)
