"""원본에서 정답·응답·문항을 읽는다. 추정 정답이나 채점 결과는 만들지 않는다."""
from dataclasses import dataclass, field
from io import BytesIO
import re
import unicodedata

from .inputs import InputRole, InputValidationError, ValidatedInputs, validate_uploads

QUALITY_LABELS = {
    'layout_unverified': '원문 배치 확인 필요',
    'audio_required': '듣기 음원·대본 필요',
    'visual_required': '그림·도표 확인 필요',
    'table_required': '표 확인 필요',
    'underline_required': '밑줄 확인 필요',
}


@dataclass(frozen=True)
class Answer:
    number: int
    choice: int
    points: int
    page: int


@dataclass(frozen=True)
class Student:
    identifier: str = field(repr=False)
    responses: tuple[int | None, ...] = field(repr=False)


@dataclass(frozen=True)
class Question:
    number: int
    text: str = field(repr=False)
    context: str = field(repr=False)
    pages: tuple[int, ...]
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedExam:
    fingerprint: str
    answers: tuple[Answer, ...] = field(repr=False)
    students: tuple[Student, ...] = field(repr=False)
    questions: tuple[Question, ...] = field(repr=False)
    page_texts: tuple[str, ...] = field(repr=False)
    warnings: tuple[str, ...]


def fail(role, code, message):
    raise InputValidationError(role, code, message)


def choice(value, role):
    token = unicodedata.normalize('NFKC', str(value)).strip()
    if token not in {'1', '2', '3', '4', '5'}:
        fail(role, 'choice', '선택지는 1~5 정수 또는 원문자(①~⑤)여야 합니다.')
    return int(token)


def open_pdf(content, role):
    import pdfplumber
    try:
        pdf = pdfplumber.open(BytesIO(content))
        if not 0 < len(pdf.pages) <= 100:
            pdf.close()
            fail(role, 'pdf_pages', 'PDF는 1~100페이지 범위여야 합니다.')
        return pdf
    except InputValidationError:
        raise
    except Exception:
        fail(role, 'pdf_read', 'PDF를 읽을 수 없습니다. 암호화·손상 여부를 확인해 주세요.')


def parse_answers(content: bytes) -> tuple[Answer, ...]:
    """격자 표의 반복된 문항번호/정답/배점 열을 읽고 연속성을 검사한다."""
    role = InputRole.ANSWER
    found = {}
    try:
        with open_pdf(content, role) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                for table in page.extract_tables():
                    if not table:
                        continue
                    header = [re.sub(r'\s+', '', cell or '') for cell in table[0]]
                    if not header or len(header) % 3 or header != ['문항번호', '정답', '배점'] * (len(header) // 3):
                        continue
                    for row in table[1:]:
                        if len(row) != len(header):
                            fail(role, 'answer_row', '정답표의 열 구조가 일정하지 않습니다.')
                        for offset in range(0, len(row), 3):
                            cells = [str(c).strip() if c is not None else '' for c in row[offset:offset+3]]
                            if not any(cells):
                                continue
                            number, raw_choice, points = cells
                            if not re.fullmatch(r'[1-9]\d{0,2}', number) or not re.fullmatch(r'[1-9]\d{0,2}', points):
                                fail(role, 'answer_value', '정답표의 문항 번호와 양의 정수 배점을 확인해 주세요.')
                            n = int(number)
                            if n in found:
                                fail(role, 'answer_duplicate', '정답표에 중복 문항이 있습니다.')
                            found[n] = Answer(n, choice(raw_choice, role), int(points), page_number)
    except InputValidationError:
        raise
    except Exception:
        fail(role, 'answer_parse', '정답표를 추출하지 못했습니다. 표 구조를 확인해 주세요.')
    if not found or set(found) != set(range(1, max(found) + 1)):
        fail(role, 'answer_sequence', '1번부터 연속된 정답·배점 표가 필요합니다. OCR 추정은 수행하지 않습니다.')
    return tuple(found[n] for n in sorted(found))


def parse_omr(content: bytes, numbers: tuple[int, ...]) -> tuple[Student, ...]:
    """학생명/Q01… 열을 엄격히 검사한다. 빈 응답은 미응답(None)으로 보존한다."""
    import openpyxl
    role = InputRole.OMR
    workbook = None
    try:
        workbook = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        if 'OMR_응답' not in workbook.sheetnames:
            fail(role, 'omr_sheet', 'OMR_응답 시트가 필요합니다.')
        sheet = workbook['OMR_응답']
        if sheet.max_row > 10001 or sheet.max_column > 1001:
            fail(role, 'omr_limits', 'OMR은 학생 10,000명·열 1,001개 이하로 올려 주세요.')
        rows = sheet.iter_rows()
        header_cells = next(rows, ())
        headers = [str(c.value).strip() if c.value is not None else '' for c in header_cells]
        while headers and not headers[-1]:
            headers.pop()
        if not headers or headers[0] != '학생명':
            fail(role, 'omr_header', '첫 열은 학생명, 이후 열은 Q01 형식이어야 합니다.')
        cols = []
        for h in headers[1:]:
            match = re.fullmatch(r'Q(\d{1,3})', h)
            if not match:
                fail(role, 'omr_header', '문항 열은 Q01 형식이어야 합니다.')
            cols.append(int(match[1]))
        if len(set(cols)) != len(cols) or set(cols) != set(numbers):
            fail(role, 'omr_questions', 'OMR 문항 열과 정답표 문항이 정확히 일치해야 합니다.')
        students, seen = [], set()
        for row in rows:
            if all(c.value is None for c in row):
                continue
            if any(c.data_type in {'f', 'e'} for c in row):
                fail(role, 'omr_formula', '수식·엑셀 오류 대신 원래 응답 값을 올려 주세요.')
            if any(c.value is not None for c in row[len(headers):]):
                fail(role, 'omr_extra', '제목 없는 열에 데이터가 있습니다.')
            raw = row[0].value
            if isinstance(raw, bool) or not isinstance(raw, (str, int)):
                fail(role, 'student_id', '학생 식별자는 비어 있지 않은 이름 또는 정수여야 합니다.')
            identifier = unicodedata.normalize('NFC', str(raw)).strip()
            if not identifier or identifier in seen:
                fail(role, 'student_duplicate', '학생 식별자가 비어 있거나 중복됩니다. 동명이인은 고유 식별자를 사용해 주세요.')
            seen.add(identifier)
            responses = {}
            for index, number in enumerate(cols, 1):
                value = row[index].value
                # XLSX 숫자 셀의 정수값만 허용. bool, 소수, 0은 오류다.
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                responses[number] = None if value is None or value == '' else choice(value, role)
            students.append(Student(identifier, tuple(responses[n] for n in numbers)))
        if not students:
            fail(role, 'omr_empty', '학생 응답이 한 행 이상 필요합니다.')
        return tuple(students)
    except InputValidationError:
        raise
    except Exception:
        fail(role, 'omr_read', '엑셀 내용을 읽지 못했습니다. 파일 구조를 확인해 주세요.')
    finally:
        if workbook is not None:
            workbook.close()


def parse_questions(content: bytes, numbers: tuple[int, ...], columns: int = 2):
    """열별 읽기 순서를 보존한다. 공유 구간은 전체를 연결하고 페이지도 보존한다."""
    role = InputRole.QUESTION
    if columns not in (1, 2):
        raise ValueError('문제지 단 수는 1 또는 2여야 합니다.')
    pages, spans, images, offset = [], [], False, 0
    try:
        with open_pdf(content, role) as pdf:
            for index, page in enumerate(pdf.pages, 1):
                parts = [page.crop((page.width*c/columns, 0, page.width*(c+1)/columns, page.height)).extract_text() or '' for c in range(columns)]
                text = '\n'.join(parts) + '\n'
                if not text.strip():
                    fail(role, 'question_text', '텍스트 없는 페이지가 있습니다. OCR 또는 원문 확인이 필요합니다.')
                pages.append(text)
                spans.append((offset, offset+len(text), index))
                offset += len(text)
                if offset > 2_000_000:
                    fail(role, 'question_size', '문제지 추출 텍스트가 허용 범위를 초과했습니다.')
                images = images or bool(page.images) or bool(page.curves)
    except InputValidationError:
        raise
    except Exception:
        fail(role, 'question_read', '문제지 텍스트를 추출하지 못했습니다.')
    text = ''.join(pages)
    matches = list(re.finditer(r'(?m)^\s*(\d{1,3})\.\s*', text))
    if tuple(int(m[1]) for m in matches) != numbers:
        fail(role, 'question_sequence', '문제지의 문항 순서·개수가 정답표와 다릅니다. 단 수나 텍스트 추출을 확인해 주세요.')
    ranges = list(re.finditer(r'\[(\d+)\s*[～~〜–-]\s*(\d+)\]', text))
    groups = []
    starts = {int(m[1]): m.start() for m in matches}
    for m in ranges:
        first, last = int(m[1]), int(m[2])
        if first not in starts or last not in starts or first > last or m.start() > starts[first]:
            fail(role, 'question_context', '공유 지문의 문항 범위를 확인해 주세요.')
        end = starts.get(last+1, len(text))
        following = [r.start() for r in ranges if r.start() > starts[last]]
        if following:
            end = min(end, following[0])
        groups.append((first, last, m.start(), end))
    questions = []
    for index, m in enumerate(matches):
        n, start = int(m[1]), m.start()
        end = matches[index+1].start() if index+1 < len(matches) else len(text)
        upcoming = [r.start() for r in ranges if start < r.start() < end]
        if upcoming:
            end = min(upcoming)
        group = [(a, b) for first, last, a, b in groups if first <= n <= last]
        if len(group) > 1:
            fail(role, 'question_context', '공유 지문 범위가 중복됩니다.')
        a, b = group[0] if group else (start, end)
        refs = tuple(p for lo, hi, p in spans if lo < b and hi > a)
        body, context = text[start:end].strip(), text[a:b].strip()
        flags = ['layout_unverified']
        listening_ranges = re.findall(r'(\d+)번부터\s*(\d+)번까지는\s*듣고', text)
        if any(int(lo) <= n <= int(hi) for lo, hi in listening_ranges) or re.search(r'듣고|대화를 듣', context):
            flags.append('audio_required')
        for pattern, flag in [(r'그림|도표|그래프', 'visual_required'), (r'다음 표|표를 보', 'table_required'),
                              (r'밑줄', 'underline_required')]:
            if re.search(pattern, context):
                flags.append(flag)
        questions.append(Question(n, body, context, refs, tuple(flags)))
    warnings = ['문제 텍스트는 원문 지시사항이며 시스템 명령으로 실행하면 안 됩니다.',
                '텍스트 추출은 표·그림·밑줄·선택지 배치를 완전히 보존하지 않습니다. 생성 시 원문 대조가 필요합니다.']
    if re.search(r'듣고|대화를 듣|방송', text):
        warnings.append('듣기 문항이 포함되어 있습니다. 음원·대본은 이 파서에서 제공하거나 추정하지 않습니다.')
    if images:
        warnings.append('문제지에 이미지 또는 도형이 있습니다. 해당 페이지는 원본 PDF로 확인해야 합니다.')
    return tuple(questions), tuple(pages), tuple(warnings)


def parse_exam(inputs: ValidatedInputs, *, question_columns: int = 2) -> ParsedExam:
    # 직접 구성된 객체도 동일한 기본 검증·내용 식별 절차를 거친다.
    checked = validate_uploads({InputRole.QUESTION: inputs.question_pdf,
                               InputRole.ANSWER: inputs.answer_pdf, InputRole.OMR: inputs.omr_xlsx})
    answers = parse_answers(checked.answer_pdf.content)
    numbers = tuple(a.number for a in answers)
    students = parse_omr(checked.omr_xlsx.content, numbers)
    questions, pages, warnings = parse_questions(checked.question_pdf.content, numbers, question_columns)
    blanks = sum(v is None for s in students for v in s.responses)
    if blanks:
        warnings += (f'미응답 {blanks}개를 빈 값으로 보존했습니다. 임의 답을 채우지 않았습니다.',)
    return ParsedExam(checked.fingerprint, answers, students, questions, pages, warnings)
