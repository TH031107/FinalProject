from dataclasses import replace
from io import BytesIO
from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import openpyxl
from academy.analysis import analysis_key, analyze
from academy.export import export_excel
from academy.grading import grade_exam
from academy.parsing import Answer, Student, Question, ParsedExam
from academy.inputs import InputRole,Upload,validate_uploads
from test_parsing import answer_pdf, question_pdf, omr


def exam():
    return ParsedExam('synthetic-fingerprint',
        tuple(Answer(i,c,p,1) for i,c,p in [(1,1,2),(2,2,3),(3,3,4),(4,4,1)]),
        (Student('A',(1,2,5,None)),Student('B',(1,None,None,None)),
         Student('C',(None,None,None,None)),Student('D',(1,2,5,None))),
        tuple(Question(i,'synthetic','synthetic',(1,),('layout_unverified',)) for i in range(1,5)),('synthetic',),())


class GradingTests(unittest.TestCase):
    def test_rank_goal_weighted_minimum_and_percentage_points(self):
        result=grade_exam(exam())
        goal=result.rank_goal('B',2)
        self.assertEqual(goal['threshold'],5)
        self.assertEqual(goal['needed_points'],3)
        self.assertEqual(goal['extra_count'],1)
        self.assertEqual(goal['percentage_points'],25)
        self.assertGreaterEqual(goal['new_score'],5)
        self.assertTrue(result.rank_goal('A',2)['already'])
        with self.assertRaises(ValueError): result.rank_goal('B',0)
        with self.assertRaises(ValueError): result.rank_goal('B',5)

    def test_hand_calculated_scores_statistics_and_ranks(self):
        r = grade_exam(exam())
        self.assertEqual([s.score for s in r.students],[5,2,0,5])
        self.assertEqual([s.rank for s in r.students],[1,3,4,1])
        self.assertEqual((r.maximum,r.average,r.median),(10,3,3.5))
        self.assertAlmostEqual(r.population_stddev, math.sqrt(4.5))
        self.assertEqual(r.students[0].earned,(2,3,0,0))
        self.assertEqual(r.top_wrong,(3,4,2))
        self.assertEqual(r.questions[3].unanswered_count,4)
        self.assertEqual(r.questions[2].wrong_rate,1)

    def test_all_correct_single_student(self):
        e = replace(exam(),students=(Student('A',(1,2,3,4)),))
        r = grade_exam(e)
        self.assertEqual((r.average,r.median,r.population_stddev),(10,10,0))
        self.assertEqual(r.top_wrong,())

    def test_top_three_ties_and_weakest_ties(self):
        e = replace(exam(),students=(Student('A',(None,)*4),Student('B',(None,)*4)))
        r = grade_exam(e)
        self.assertEqual(r.top_wrong,(1,2,3))
        self.assertEqual(r.top_boundary_ties,(1,2,3,4))
        self.assertEqual(len(r.weakest()),2)
        self.assertEqual(r.student(' A ').score,0)
        with self.assertRaises(LookupError):
            r.student('unknown')

    def test_changed_answer_changes_score_without_hardcoding(self):
        e = exam()
        changed = replace(e,answers=(replace(e.answers[0],choice=5),)+e.answers[1:])
        self.assertEqual(grade_exam(changed).students[0].score,3)

    def test_invalid_programmatic_input_stops(self):
        e = exam()
        for bad in [replace(e,students=()),replace(e,answers=e.answers[:-1]),
                    replace(e,students=(Student('A',(True,2,3,4)),)),
                    replace(e,students=(Student('A',(1,)),)),
                    replace(e,students=(e.students[0],e.students[0]))]:
            with self.subTest(),self.assertRaises(ValueError):
                grade_exam(bad)

    def test_workbook_roundtrip_values_and_no_formula_execution(self):
        e = replace(exam(),students=(Student('=synthetic(1)',(1,2,None,4)),))
        r = grade_exam(e)
        w = openpyxl.load_workbook(BytesIO(export_excel(r,e)),data_only=False)
        self.assertEqual(len(w.sheetnames),5)
        self.assertEqual(w['성적 개요']['B5'].value,6)
        self.assertEqual(w['학생 성적']['B2'].value,6)
        self.assertEqual(w['학생 성적']['A2'].value,'=synthetic(1)')
        self.assertEqual(w['학생 성적']['A2'].data_type,'s')
        self.assertEqual(w['문항별 정답 여부']['D2'].value,'미응답')
        self.assertFalse(any(c.data_type=='f' for s in w for row in s for c in row))
        w.close()
        with self.assertRaises(ValueError):
            export_excel(r,replace(e,fingerprint='other'))

    def test_analyze_bundle_and_settings_key(self):
        inputs = validate_uploads({InputRole.QUESTION:Upload('q.pdf',question_pdf()),
            InputRole.ANSWER:Upload('a.pdf',answer_pdf([['1','1','2','3','3','4'],['2','2','3','','','']])),
            InputRole.OMR:Upload('s.xlsx',omr())})
        bundle = analyze(inputs)
        self.assertEqual(bundle.grades.students[0].score,6)
        self.assertEqual(bundle.key,analysis_key(inputs))
        self.assertNotEqual(bundle.key,analysis_key(inputs,1))
        self.assertNotIn('synthetic-A',repr(bundle))

    def test_quality_flags_for_listening_and_visual_dependencies(self):
        # 실제 PDF 파서로 만든 문항별 품질 결과를 검증한다.
        from reportlab.pdfgen import canvas
        from academy.parsing import parse_questions
        data=BytesIO(); c=canvas.Canvas(data)
        t=c.beginText(40,700); t.setFont('HYSMyeongJo-Medium',10)
        for line in ['1번부터 2번까지는 듣고 답합니다.','1. 다음을 듣고 답하시오.','2. 그림과 다음 표를 보고 밑줄을 확인하시오.']:
            t.textLine(line)
        c.drawText(t);c.save()
        qs,_,_=parse_questions(data.getvalue(),(1,2),columns=1)
        self.assertIn('audio_required',qs[1].quality_flags)
        self.assertTrue({'visual_required','table_required','underline_required'}.issubset(qs[1].quality_flags))


if __name__=='__main__':
    unittest.main()
