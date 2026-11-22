"""Acceptance policy distinct from the measured transition classes."""
from pathlib import Path

from . import Outcome, Policy, RunSpec, Status, measure
from .core import Measurement, classify, execute


def accepted(result, allow_pass_to_pass=False):
    allowed = {Outcome.FAIL_TO_PASS}
    if allow_pass_to_pass:
        allowed.add(Outcome.PASS_TO_PASS)
    return (bool(result.results) and all(r.outcome in allowed for r in result.results)
            and all(not report.errors and not report.compile_suites
                    and Status.FAIL not in report.tests.values() for report in result.head))


def check(adapter, command, base, head, selected=None, policy=Policy()):
    if selected is not None:
        return measure(adapter, RunSpec(command, base), RunSpec(command, head), selected, policy)
    # Discover from the two required head reports; do not add a third discovery invocation.
    h = [adapter.parse(execute(RunSpec(command, head), policy.timeout), Path(head))
         for _ in range(policy.repeats_head)]
    selected = set().union(*(r.tests for r in h))
    if not selected:
        raise ValueError('head command reported no tests')
    b = [adapter.parse(execute(RunSpec(command, base), policy.timeout), Path(base))]
    if any(b[0].status(test) in (Status.FAIL, Status.COMPILE) for test in selected):
        b += [adapter.parse(execute(RunSpec(command, base), policy.timeout), Path(base))
              for _ in range(policy.retries_base)]
    return Measurement([classify(test, b, h) for test in sorted(selected)], b, h)
