from dataclasses import dataclass
import math
import os
from pathlib import Path
import signal
import subprocess
from typing import Callable, Sequence

from .adapters import Adapter
from .model import Capture, Outcome, Report, Result, Status, TestId


@dataclass(frozen=True)
class RunSpec:
    command: Sequence[str]
    cwd: Path


@dataclass(frozen=True)
class Policy:
    retries_base: int = 3
    repeats_head: int = 2
    timeout: float = 600

    def __post_init__(self):
        if (type(self.retries_base) is not int or not 1 <= self.retries_base <= 3 or
                type(self.repeats_head) is not int or self.repeats_head != 2):
            raise ValueError("policy allows 1..3 base retries and exactly 2 head runs")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 600:
            raise ValueError("timeout must be in (0, 600]")


def execute(spec: RunSpec, timeout: float) -> Capture:
    try:
        process = subprocess.Popen(spec.command, cwd=spec.cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return Capture("", str(exc), 127)
    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = process.communicate()
    return Capture(out.decode(errors="replace"), err.decode(errors="replace"),
                   process.returncode, timed_out)


def classify(test: TestId, base: list[Report], head: list[Report]) -> Result:
    b, h = [r.status(test) for r in base], [r.status(test) for r in head]
    if (not b or len(h) < 2 or Status.UNKNOWN in b + h or
            (len(b) < 2 and b[0] in (Status.FAIL, Status.COMPILE))):
        outcome = Outcome.UNJUDGED
    elif len(set(b)) > 1 or len(set(h)) > 1:
        outcome = Outcome.FLAKY
    elif h[0] == Status.PASS:
        outcome = {Status.FAIL: Outcome.FAIL_TO_PASS, Status.COMPILE: Outcome.COMPILE_ONLY,
                   Status.SKIP: Outcome.SKIP_TO_PASS, Status.PASS: Outcome.PASS_TO_PASS}[b[0]]
    elif h[0] in (Status.FAIL, Status.COMPILE):
        outcome = Outcome.PASS_TO_FAIL if b[0] == Status.PASS else Outcome.FAIL_TO_FAIL
    else:
        outcome = Outcome.UNJUDGED
    return Result(test, outcome, b, h)


@dataclass
class Measurement:
    results: list[Result]
    base: list[Report]
    head: list[Report]

    def as_dict(self):
        return {"results": [r.as_dict() for r in self.results],
                "runs": {"base": [r.as_dict() for r in self.base],
                         "head": [r.as_dict() for r in self.head]}}


def measure(adapter: Adapter, base: RunSpec, head: RunSpec, selected: set[TestId],
            policy: Policy = Policy(), runner: Callable = execute) -> Measurement:
    if not selected:
        raise ValueError("select at least one test")

    def run(spec):
        return adapter.parse(runner(spec, policy.timeout), Path(spec.cwd))

    b = [run(base)]
    if any(b[0].status(test) in (Status.FAIL, Status.COMPILE) for test in selected):
        b += [run(base) for _ in range(policy.retries_base)]
    h = [run(head) for _ in range(policy.repeats_head)]
    return Measurement([classify(test, b, h) for test in sorted(selected)], b, h)
