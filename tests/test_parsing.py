"""원본 과제 답안/학생 데이터가 없는 합성 PDF·XLSX 검증."""
from io import BytesIO
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import openpyxl
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from academy.inputs import InputRole, InputValidationError, Upload, validate_uploads
from academy.parsing import parse_answers, parse_omr, parse_questions, parse_exam

pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))


def answer_pdf(rows):
    data = BytesIO()
    c = canvas.Canvas(data, pagesize=(600, 700))
    c.setFont('HYSMyeongJo-Medium', 10)
    all_rows = [['문항번호', '정답', '배점'] * 2] + rows
    for i in range(len(all_rows)+1):
        c.line(40, 650-i*30, 580, 650-i*30)
    for i in range(7):
        c.line(40+i*90, 650, 40+i*90, 650-len(all_rows)*30)
    for r, row in enumerate(all_rows):
        for col, value in enumerate(row):
            c.drawString(48+col*90, 630-r*30, value)
    c.save()
    return data.getvalue()


def question_pdf(left='1. alpha\n2. beta', right='3. gamma'):
    data = BytesIO()
    c = canvas.Canvas(data, pagesize=(600, 700))
    for x, text in [(40, left), (340, right)]:
        t = c.beginText(x, 650)
        t.setFont('Helvetica', 10)
        for line in text.splitlines():
            t.textLine(line)
        c.drawText(t)
    c.save()
    return data.getvalue()


def omr(rows=None, headers=None):
    w = openpyxl.Workbook()
    s = w.active
    s.title = 'OMR_응답'
    s.append(headers or ['학생명', 'Q01', 'Q02', 'Q03'])
    for row in rows if rows is not None else [['synthetic-A', 1, None, 3]]:
        s.append(row)
    data = BytesIO()
    w.save(data)
    w.close()
    return data.getvalue()


class ParsingTests(unittest.TestCase):
    def test_answer_columns_and_changed_values(self):
        rows = [['1','2','4','3','1','7'], ['2','5','6','','','']]
        result = parse_answers(answer_pdf(rows))
        self.assertEqual([(a.number,a.choice,a.points) for a in result], [(1,2,4),(2,5,6),(3,1,7)])
        rows[0][1] = '4'
        self.assertEqual(parse_answers(answer_pdf(rows))[0].choice, 4)

    def test_invalid_answer_tables_stop(self):
        for rows in [[['1','2','4','1','1','7']], [['1','2','4','3','1','7']], [['1','9','2','','','']], [['1','2','0','','','']]]:
            with self.subTest(rows=rows), self.assertRaises(InputValidationError):
                parse_answers(answer_pdf(rows))

    def test_omr_reordered_columns_and_blanks(self):
        students = parse_omr(omr([['synthetic-A','③',1,None]], ['학생명','Q03','Q01','Q02']), (1,2,3))
        self.assertEqual(students[0].responses, (1,None,3))
        self.assertNotIn('synthetic-A', repr(students))

    def test_omr_rejects_wrong_schema_values_and_duplicates(self):
        cases = [omr(headers=['학생명','Q01','Q01','Q03']), omr(headers=['학생명','Q01','Q02','Q04']),
                 omr([['A',1,2,3],[' A ',1,2,3]]), omr([[None,1,2,3]]),
                 omr([['A','=1+1',2,3]]), omr([['A',True,2,3]]), omr([['A',0,2,3]]), omr([['A',1.5,2,3]])]
        for data in cases:
            with self.subTest(), self.assertRaises(InputValidationError):
                parse_omr(data, (1,2,3))

    def test_question_columns_and_shared_context(self):
        data = question_pdf('[1~2] Shared passage\n1. alpha\n2. beta', '3. gamma')
        qs,pages,warnings = parse_questions(data,(1,2,3))
        self.assertIn('Shared passage', qs[0].context)
        self.assertEqual(qs[0].context, qs[1].context)
        self.assertNotIn('3. gamma', qs[0].context)
        self.assertNotIn('Shared passage', qs[2].context)
        self.assertEqual(qs[0].pages,(1,))
        self.assertEqual(len(pages),1)
        self.assertTrue(warnings)

    def test_question_mismatch_and_bad_pdf_stop(self):
        for data in [question_pdf(), b'%PDF-not-valid', question_pdf('1. a\n1. b','3. c')]:
            with self.subTest(), self.assertRaises(InputValidationError):
                parse_questions(data,(1,2))

    def test_shared_passage_crosses_pages(self):
        data = BytesIO()
        c = canvas.Canvas(data, pagesize=(600,700))
        for lines in [['[1~2] Shared text', '1. alpha'], ['continued passage', '2. beta', '3. gamma']]:
            t = c.beginText(40,650)
            t.setFont('Helvetica',10)
            for line in lines:
                t.textLine(line)
            c.drawText(t)
            c.showPage()
        c.save()
        qs,_,_ = parse_questions(data.getvalue(),(1,2,3),columns=1)
        self.assertEqual(qs[0].pages,(1,2))
        self.assertEqual(qs[0].context, qs[1].context)
        self.assertIn('continued passage',qs[0].context)
        self.assertNotIn('3. gamma',qs[0].context)

    def test_encrypted_pdf_stops_without_exposing_password(self):
        data = BytesIO()
        c = canvas.Canvas(data, encrypt='synthetic-password')
        c.drawString(40,600,'1. alpha')
        c.save()
        with self.assertRaises(InputValidationError) as caught:
            parse_questions(data.getvalue(),(1,))
        self.assertNotIn('synthetic-password',str(caught.exception))

    def test_end_to_end_and_private_repr(self):
        uploads = {InputRole.QUESTION: Upload('q.pdf',question_pdf()),
                   InputRole.ANSWER: Upload('a.pdf',answer_pdf([['1','2','2','3','3','3'],['2','1','2','','','']])),
                   InputRole.OMR: Upload('s.xlsx',omr())}
        result = parse_exam(validate_uploads(uploads))
        self.assertEqual(len(result.questions),3)
        self.assertEqual(len(result.students),1)
        self.assertNotIn('synthetic-A',repr(result))
        self.assertTrue(any('미응답' in w for w in result.warnings))


if __name__ == '__main__':
    unittest.main()
