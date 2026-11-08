"""단계별 구현에서 공유하는 정책. 인증값이나 과제 정답을 저장하지 않는다."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class InputLimits:
    max_file_bytes: int = 20 * 1024 * 1024
    max_xlsx_unpacked_bytes: int = 80 * 1024 * 1024
    max_xlsx_members: int = 2000


@dataclass(frozen=True)
class ModelPolicy:
    """후보 정책이며 아직 API 접근·정확도·응답시간 검증을 거치지 않았다."""
    primary: str = "gpt-6.1-sol"
    complex: str = "gpt-6-astra"
    deadline_seconds: float = 18.0
    generation_effort: str = "low"

    def __post_init__(self):
        allowed = {"gpt-6.1-sol", "gpt-6-astra"}
        if self.primary not in allowed or self.complex not in allowed:
            raise ValueError("검토한 정확도 중심 모델 후보만 지정할 수 있습니다.")
        if not 0 < self.deadline_seconds < 20:
            raise ValueError("내부 시간 한도는 평가 제한인 20초보다 작아야 합니다.")
        if self.generation_effort not in {"low", "medium", "high"}:
            raise ValueError("생성 추론 강도는 low/medium/high 중 하나여야 합니다.")


@dataclass(frozen=True)
class RetryPolicy:
    """최대 최초+수정1회. 시간 값은 검증 전 보수적 정책이며 완료 보장이 아니다."""
    max_attempts: int = 2
    minimum_remaining_seconds: float = 6.0
    safety_margin_seconds: float = 1.0
    duration_multiplier: float = 1.25

    def __post_init__(self):
        if type(self.max_attempts) is not int or self.max_attempts not in (1,2):
            raise ValueError('생성은 최초 포함1~2회만 허용합니다.')
        values=(self.minimum_remaining_seconds,self.safety_margin_seconds,self.duration_multiplier)
        if any(not math.isfinite(v) or v<=0 for v in values) or self.duration_multiplier<1:
            raise ValueError('재시도 시간 정책을 확인해 주세요.')

    def required_seconds(self, generation_seconds, review_seconds):
        # 검수까지 도달하지 않았다면 생성 시간 또는 최소2초를 검수 추정치로 사용한다.
        estimated_review=review_seconds if review_seconds is not None else max(2.0,generation_seconds)
        return max(self.minimum_remaining_seconds,
                   (generation_seconds+estimated_review)*self.duration_multiplier+self.safety_margin_seconds)
