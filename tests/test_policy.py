import unittest
from fail_to_pass import Outcome, Report, Result, Status, TestId
from fail_to_pass.check import accepted
from fail_to_pass.core import Measurement


class PolicyTests(unittest.TestCase):
    def test_only_confirmed_assertion_red_is_regression_evidence(self):
        identity = TestId('guard.py', 'checks')
        for outcome in Outcome:
            result = Measurement([Result(identity, outcome, [], [])], [], [Report({identity: Status.PASS})])
            with self.subTest(outcome=outcome):
                self.assertEqual(accepted(result), outcome == Outcome.FAIL_TO_PASS)
                self.assertEqual(accepted(result, True), outcome in (Outcome.FAIL_TO_PASS, Outcome.PASS_TO_PASS))
        self.assertFalse(accepted(Measurement([], [], []), True))

    def test_p2p_permission_cannot_mask_unselected_head_failure_or_parser_error(self):
        identity, other = TestId('guard.py', 'checks'), TestId('guard.py', 'other')
        for report in (Report({identity: Status.PASS, other: Status.FAIL}),
                       Report({identity: Status.PASS}, errors=['malformed report']),
                       Report({identity: Status.PASS}, compile_suites={'another'})):
            result = Measurement([Result(identity, Outcome.PASS_TO_PASS, [], [])], [], [report])
            self.assertFalse(accepted(result, True))
