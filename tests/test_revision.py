import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from academy.chat import ask, Review
from academy.config import RetryPolicy
import test_chat as fixtures
from test_chat import FakeBackend, task


class Repairable(FakeBackend):
    def __init__(self,mode='review',always_fail=False,tasks=None):
        super().__init__(tasks or [task('generate')])
        self.mode=mode;self.always_fail=always_fail;self.generated=0

    async def complete(self,kind,payload,schema,**kwargs):
        result=await super().complete(kind,payload,schema,**kwargs)
        if kind=='generate':
            self.generated+=1
            bad=self.generated==1 or self.always_fail
            result=result.model_copy(update={'passage':'first_generated' if self.generated==1 else 'fixed_generated'})
            if self.mode=='choices' and bad:return result.model_copy(update={'choices':['duplicate']*5})
            if self.mode=='schema' and bad:return result.model_copy(update={'answer':9})
        if kind=='review' and self.mode=='review':
            return Review(approved=self.generated>1 and not self.always_fail,
                          reason='Explain the unique answer. user@example.com 010-1234-5678')
        return result


class RevisionTests(unittest.IsolatedAsyncioTestCase):
    def bundle(self):return fixtures.ChatTests().bundle()

    async def test_review_feedback_repairs_once_and_only_approved_draft_is_shown(self):
        backend=Repairable()
        reply=await ask(self.bundle(),'synthetic',[],backend)
        self.assertEqual(reply.status,'ok');self.assertEqual(reply.model_calls,5)
        self.assertNotIn('first_generated',reply.text);self.assertIn('fixed_generated',reply.text)
        self.assertEqual(reply.text.count('기준 문항:'),1)
        generations=[p for kind,p,_ in backend.calls if kind=='generate']
        self.assertNotIn('feedback',generations[0])
        self.assertIn('unique answer',generations[1]['feedback'])
        self.assertNotIn('user@example.com',generations[1]['feedback'])
        self.assertNotIn('010-1234',generations[1]['feedback'])
        self.assertIsNotNone(generations[1]['previous_draft'])
        self.assertEqual(generations[0]['source'],generations[1]['source'])
        self.assertTrue(any(s.stage=='retry_gate' and s.status=='retry' for s in reply.steps))

    async def test_repeated_rejection_stops_at_two_attempts(self):
        b=Repairable(always_fail=True);reply=await ask(self.bundle(),'synthetic',[],b)
        self.assertEqual(b.generated,2);self.assertEqual(reply.model_calls,5)
        self.assertEqual(reply.status,'error');self.assertNotIn('generated',reply.text)
        self.assertIn('횟수 한도',reply.text)
        self.assertTrue(any(s.stage=='retry_gate' and s.status=='attempt_limit' for s in reply.steps))

    async def test_bad_choices_repair_before_review(self):
        b=Repairable(mode='choices');reply=await ask(self.bundle(),'synthetic',[],b)
        self.assertEqual(reply.status,'ok');self.assertEqual(reply.model_calls,4)
        self.assertEqual([k for k,_,_ in b.calls],['route','generate','generate','review'])
        self.assertIn('선택지5개',b.calls[2][1]['feedback'])

    async def test_schema_error_repair_counts_failed_attempt_and_hides_bad_object(self):
        b=Repairable(mode='schema');reply=await ask(self.bundle(),'synthetic',[],b)
        self.assertEqual(reply.status,'ok');self.assertEqual(b.generated,2)
        self.assertIsNone(b.calls[2][1]['previous_draft'])
        self.assertIn('스키마',b.calls[2][1]['feedback'])
        self.assertNotIn('정답: 9',reply.text)

    async def test_insufficient_remaining_budget_skips_retry(self):
        b=Repairable();policy=RetryPolicy(minimum_remaining_seconds=100)
        reply=await ask(self.bundle(),'synthetic',[],b,retry_policy=policy)
        self.assertEqual(b.generated,1);self.assertEqual(reply.model_calls,3)
        self.assertIn('시간 여유가 부족',reply.text)
        self.assertTrue(any(s.status=='insufficient_time' for s in reply.steps))

    async def test_one_attempt_policy_supports_baseline_comparison(self):
        b=Repairable();reply=await ask(self.bundle(),'synthetic',[],b,retry_policy=RetryPolicy(max_attempts=1))
        self.assertEqual(b.generated,1);self.assertEqual(reply.model_calls,3)

    async def test_api_connection_failure_is_not_retried(self):
        class ConnectionFailure(FakeBackend):
            async def complete(self,kind,payload,schema,**kw):
                if kind=='generate':
                    self.calls.append((kind,payload,kw));raise RuntimeError('synthetic authentication error')
                return await super().complete(kind,payload,schema,**kw)
        b=ConnectionFailure([task('generate')]);reply=await ask(self.bundle(),'synthetic',[],b)
        self.assertEqual([k for k,_,_ in b.calls],['route','generate'])
        self.assertEqual(reply.status,'error');self.assertNotIn('authentication',reply.text)

    async def test_timeout_during_retry_preserves_completed_statistics(self):
        class SlowRepair(Repairable):
            async def complete(self,kind,payload,schema,**kw):
                result=await super().complete(kind,payload,schema,**kw)
                if kind=='generate' and self.generated==2:await asyncio.sleep(2)
                return result
        b=SlowRepair(tasks=[task('statistics'),task('generate')])
        reply=await ask(self.bundle(),'synthetic',[],b,deadline=.15,
                        retry_policy=RetryPolicy(minimum_remaining_seconds=.001,safety_margin_seconds=.001))
        self.assertEqual(reply.status,'timeout');self.assertEqual(b.generated,2)
        self.assertIn('3.00',reply.text);self.assertNotIn('first_generated',reply.text)

    async def test_failed_retries_do_not_duplicate_or_drop_other_tasks(self):
        b=Repairable(always_fail=True,tasks=[task('generate'),task('statistics')])
        reply=await ask(self.bundle(),'synthetic',[],b)
        self.assertEqual(reply.status,'partial');self.assertIn('3.00',reply.text)
        self.assertEqual(sum(s.stage=='advance' for s in reply.steps),2)

    def test_policy_uses_observed_durations_and_rejects_unbounded_settings(self):
        self.assertAlmostEqual(RetryPolicy().required_seconds(2,3),7.25)
        self.assertAlmostEqual(RetryPolicy().required_seconds(3,None),8.5)
        for kwargs in [{'max_attempts':3},{'max_attempts':0},{'minimum_remaining_seconds':float('nan')},
                       {'safety_margin_seconds':0},{'duration_multiplier':.5}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):RetryPolicy(**kwargs)


if __name__=='__main__':unittest.main()
