from io import BytesIO
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from academy.config import InputLimits, ModelPolicy
from academy.inputs import InputRole, Upload, InputValidationError, validate_uploads


def workbook_stub():
    # 기본 컨테이너 검사만을 위한 합성 표본. 실제 Excel 내용 파싱을 검증하지 않는다.
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return stream.getvalue()


def uploads():
    return {InputRole.QUESTION: Upload("question.pdf", b"%PDF-1.7\nquestion"),
            InputRole.ANSWER: Upload("answer.pdf", b"%PDF-1.7\nanswer"),
            InputRole.OMR: Upload("omr.xlsx", workbook_stub())}


class UploadTests(unittest.TestCase):
    def test_required_file_and_safe_error(self):
        values = uploads()
        del values[InputRole.ANSWER]
        with self.assertRaises(InputValidationError) as caught:
            validate_uploads(values)
        self.assertEqual(caught.exception.role, InputRole.ANSWER)
        self.assertEqual(caught.exception.code, "missing")

    def test_fingerprint_uses_content_and_file_roles(self):
        values = uploads()
        first = validate_uploads(values).fingerprint
        values[InputRole.QUESTION] = Upload("renamed.pdf", values[InputRole.QUESTION].content)
        self.assertEqual(first, validate_uploads(values).fingerprint)
        values[InputRole.QUESTION], values[InputRole.ANSWER] = values[InputRole.ANSWER], values[InputRole.QUESTION]
        self.assertNotEqual(first, validate_uploads(values).fingerprint)

    def test_empty_spoofed_and_oversized_files_are_rejected(self):
        for file, code in [(Upload("private-name.pdf", b""), "empty"),
                           (Upload("private-name.pdf", b"not PDF"), "invalid_pdf"),
                           (Upload("private-name.csv", b"%PDF-1.7"), "extension")]:
            values = uploads(); values[InputRole.QUESTION] = file
            with self.assertRaises(InputValidationError) as caught:
                validate_uploads(values)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private-name", str(caught.exception))
        with self.assertRaises(InputValidationError):
            validate_uploads(uploads(), InputLimits(max_file_bytes=5))

    def test_xlsx_container_and_unpacked_size(self):
        values = uploads(); values[InputRole.OMR] = Upload("omr.xlsx", b"not a workbook")
        with self.assertRaises(InputValidationError): validate_uploads(values)
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive: archive.writestr("random.xml", "data")
        values[InputRole.OMR] = Upload("omr.xlsx", stream.getvalue())
        with self.assertRaises(InputValidationError): validate_uploads(values)
        with self.assertRaises(InputValidationError):
            validate_uploads(uploads(), InputLimits(max_xlsx_unpacked_bytes=1))

    def test_payload_and_filename_not_in_repr(self):
        item = Upload("sensitive-filename.pdf", b"private-document-data")
        self.assertNotIn("sensitive", repr(item))
        self.assertNotIn("private", repr(item))

    def test_model_candidates_and_deadline_cannot_silently_downgrade(self):
        self.assertEqual(ModelPolicy().primary, "gpt-6.1-sol")
        with self.assertRaises(ValueError): ModelPolicy(primary="gpt-5.4-mini")
        with self.assertRaises(ValueError): ModelPolicy(deadline_seconds=20)


if __name__ == "__main__": unittest.main()
