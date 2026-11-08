"""업로드 파일의 기본 형식과 크기를 검사한다. 채점·내용 파싱은 다음 단계다."""
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
from typing import Mapping
import zipfile

from .config import InputLimits


class InputRole(str, Enum):
    QUESTION = "question_pdf"
    ANSWER = "answer_pdf"
    OMR = "omr_xlsx"


LABELS = {InputRole.QUESTION: "문제 PDF", InputRole.ANSWER: "정답 PDF", InputRole.OMR: "OMR 엑셀"}


@dataclass(frozen=True)
class Upload:
    # 파일명과 본문에는 개인정보가 있을 수 있으므로 기본 repr에 표시하지 않는다.
    filename: str = field(repr=False)
    content: bytes = field(repr=False)


class InputValidationError(ValueError):
    def __init__(self, role: InputRole, code: str, message: str):
        self.role, self.code = role, code
        super().__init__(f"{LABELS[role]}: {message}")


@dataclass(frozen=True)
class ValidatedInputs:
    question_pdf: Upload = field(repr=False)
    answer_pdf: Upload = field(repr=False)
    omr_xlsx: Upload = field(repr=False)
    fingerprint: str


def _check_xlsx(upload: Upload, limits: InputLimits):
    try:
        with zipfile.ZipFile(BytesIO(upload.content)) as archive:
            members = archive.infolist()
            if len(members) > limits.max_xlsx_members or sum(m.file_size for m in members) > limits.max_xlsx_unpacked_bytes:
                raise InputValidationError(InputRole.OMR, "xlsx_limits", "엑셀 내부 데이터 크기가 허용 범위를 초과했습니다.")
            names = [m.filename for m in members]
            if len(names) != len(set(names)):
                raise ValueError("duplicate members")
            if any(m.flag_bits & 1 for m in members):
                raise ValueError("encrypted archive")
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                raise ValueError("missing workbook")
    except (zipfile.BadZipFile, ValueError, RuntimeError) as exc:
        if isinstance(exc, InputValidationError):
            raise
        raise InputValidationError(InputRole.OMR, "invalid_xlsx", "정상적인 .xlsx 파일을 올려 주세요.") from None


def validate_uploads(uploads: Mapping[InputRole, Upload], limits: InputLimits | None = None) -> ValidatedInputs:
    """필수 3파일을 검사한다. 통과는 '파일 준비'이며 채점 성공을 뜻하지 않는다."""
    limits = limits or InputLimits()
    digest = hashlib.sha256(b"academy-inputs-v1")
    for role in InputRole:
        upload = uploads.get(role)
        if upload is None:
            raise InputValidationError(role, "missing", "파일을 업로드해 주세요.")
        if not isinstance(upload, Upload) or not isinstance(upload.content, bytes):
            raise InputValidationError(role, "invalid_input", "업로드 데이터를 확인해 주세요.")
        if not upload.content:
            raise InputValidationError(role, "empty", "빈 파일은 사용할 수 없습니다.")
        if len(upload.content) > limits.max_file_bytes:
            raise InputValidationError(role, "too_large", "파일 크기가 허용 범위를 초과했습니다.")
        extension = PurePosixPath(upload.filename.replace("\\", "/")).suffix.lower()
        expected = ".xlsx" if role == InputRole.OMR else ".pdf"
        if extension != expected:
            raise InputValidationError(role, "extension", f"{expected} 형식으로 올려 주세요.")
        if role == InputRole.OMR:
            _check_xlsx(upload, limits)
        elif not upload.content.startswith(b"%PDF-"):
            raise InputValidationError(role, "invalid_pdf", "PDF 형식을 확인해 주세요.")
        digest.update(role.value.encode("ascii"))
        digest.update(hashlib.sha256(upload.content).digest())
    return ValidatedInputs(uploads[InputRole.QUESTION], uploads[InputRole.ANSWER],
                           uploads[InputRole.OMR], digest.hexdigest())
