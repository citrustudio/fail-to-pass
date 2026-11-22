from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    COMPILE = "compile"
    UNKNOWN = "unknown"


class Outcome(str, Enum):
    FAIL_TO_PASS = "fail-to-pass"
    COMPILE_ONLY = "compile-only-red"
    SKIP_TO_PASS = "skip-to-pass"
    PASS_TO_FAIL = "pass-to-fail"
    FLAKY = "flaky"
    UNJUDGED = "unjudged"
    PASS_TO_PASS = "p2p"
    FAIL_TO_FAIL = "f2f"


@dataclass(frozen=True, order=True)
class TestId:
    suite: str
    name: str

    def __post_init__(self):
        if not all(isinstance(value, str) and value.strip() for value in (self.suite, self.name)):
            raise ValueError("test suite and name must be nonempty strings")

    def as_dict(self):
        return {"suite": self.suite, "name": self.name}


@dataclass
class Report:
    tests: dict[TestId, Status] = field(default_factory=dict)
    compile_suites: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def status(self, test: TestId) -> Status:
        if self.errors:
            return Status.UNKNOWN
        return self.tests.get(test, Status.COMPILE if test.suite in self.compile_suites
                              else Status.UNKNOWN)

    def as_dict(self):
        return {"tests": [{**test.as_dict(), "status": status.value}
                          for test, status in sorted(self.tests.items())],
                "compile_suites": sorted(self.compile_suites), "errors": self.errors}


@dataclass(frozen=True)
class Capture:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


@dataclass
class Result:
    test: TestId
    outcome: Outcome
    base: list[Status]
    head: list[Status]

    def as_dict(self):
        return {**self.test.as_dict(), "class": self.outcome.value,
                "base": [s.value for s in self.base], "head": [s.value for s in self.head]}
