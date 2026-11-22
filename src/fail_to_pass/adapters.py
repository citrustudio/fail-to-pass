import json
from pathlib import Path
from typing import Protocol

from .model import Capture, Report, Status, TestId


class Adapter(Protocol):
    def parse(self, capture: Capture, cwd: Path) -> Report: ...


def _check(report: Report, capture: Capture, failed: bool):
    if capture.timed_out:
        report.errors.append("invocation timed out")
    elif capture.returncode < 0:
        report.errors.append("runner terminated by signal")
    elif bool(capture.returncode) != failed:
        report.errors.append("exit status disagrees with report")
    if not report.tests and not report.compile_suites:
        report.errors.append("no test results")
    return report


def _put(report: Report, test: TestId, status: Status):
    if test in report.tests:
        report.errors.append("duplicate test identity: " + repr(test))
    report.tests[test] = status


class GoJSON:
    def parse(self, capture: Capture, cwd: Path) -> Report:
        report = Report()
        terminals = {}
        started = set()
        failed_builds = set()
        try:
            for line in capture.stdout.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                text_fields = ("Action", "Package", "ImportPath", "Test", "Output", "FailedBuild")
                if not isinstance(event, dict) or any(
                        not isinstance(event[key], str) for key in text_fields if key in event):
                    raise ValueError("event identity fields must be strings")
                action = event.get("Action")
                suite = event.get("Package") or event.get("ImportPath", "")
                if (not action or not action.strip() or not suite.strip() or
                        (action in ("run", "pause", "cont") and not event.get("Test", "").strip())):
                    raise ValueError("event lacks a required action, package or test identity")
                name = event.get("Test")
                if action == "output" and "(cached)" in event.get("Output", "") and not name:
                    report.errors.append("cached Go results; use -count=1")
                if action == "build-fail" and suite:
                    failed_builds.add(suite)
                if action == "fail" and event.get("FailedBuild") and suite:
                    failed_builds.add(suite)
                if not name and action == "output" and "[build failed]" in event.get("Output", ""):
                    failed_builds.add(suite)
                if name and suite:
                    test = TestId(suite, name)
                    if action == "run":
                        started.add(test)
                    elif action in ("pass", "fail", "skip"):
                        _put(report, test, Status(action))
                elif suite and action in ("pass", "fail", "skip"):
                    if suite in terminals:
                        report.errors.append("duplicate suite terminal event: " + suite)
                    terminals[suite] = action
        except (ValueError, TypeError, AttributeError):
            report.errors.append("malformed Go JSON stream")
        report.compile_suites = failed_builds
        if started - report.tests.keys():
            report.errors.append("tests lack terminal events")
        if report.tests.keys() - started:
            report.errors.append("terminal test events lack run events")
        for test, status in report.tests.items():
            if test.suite in failed_builds:
                report.errors.append("build failure contradicts executed tests")
            if test.suite not in terminals:
                report.errors.append("suite lacks terminal event: " + test.suite)
            elif terminals[test.suite] != "fail" and status == Status.FAIL:
                report.errors.append("non-failing suite contains a failing test")
            if terminals.get(test.suite) == "skip" and status != Status.SKIP:
                report.errors.append("skipped suite contains an executed test")
        for suite, status in terminals.items():
            if suite in failed_builds and status != "fail":
                report.errors.append("build failure contradicts suite terminal: " + suite)
            if status == "fail" and suite not in failed_builds and not any(
                    t.suite == suite and s == Status.FAIL for t, s in report.tests.items()):
                report.errors.append("suite failed without a test failure: " + suite)
        failed = bool(failed_builds) or "fail" in terminals.values()
        return _check(report, capture, failed)


class VitestJSON:
    def parse(self, capture: Capture, cwd: Path) -> Report:
        report = Report()
        failed = False
        try:
            data = json.loads(capture.stdout)
            if not isinstance(data.get("success"), bool):
                raise ValueError("missing success")
            for suite in data["testResults"]:
                if suite["status"] not in ("passed", "failed"):
                    raise ValueError("unknown suite status")
                if not isinstance(suite["name"], str) or not suite["name"].strip():
                    raise ValueError("missing test-file identity")
                path = Path(suite["name"])
                path = path if path.is_absolute() else cwd / path
                name = path.resolve().relative_to(cwd.resolve()).as_posix()
                if name == ".":
                    raise ValueError("test-file identity names the run directory")
                assertions = suite["assertionResults"]
                if suite.get("status") == "failed" and not any(
                        t.get("status") == "failed" for t in assertions):
                    # Hook and load failures have no attributable per-test assertion failure.
                    report.errors.append("suite failed without a test-level failure: " + name)
                for test in assertions:
                    status = {"passed": Status.PASS, "failed": Status.FAIL,
                              "skipped": Status.SKIP,
                              "todo": Status.SKIP, "disabled": Status.SKIP}.get(test["status"])
                    if status is None:
                        raise ValueError("unknown test status")
                    _put(report, TestId(name, test["fullName"]), status)
                    failed |= status == Status.FAIL
                if suite.get("status") == "passed" and any(
                        t["status"] == "failed" for t in assertions):
                    report.errors.append("passing suite contains a failing test")
            if data.get("numRuntimeErrorTestSuites", 0) or data.get("unhandledErrors"):
                report.errors.append("runner reported unhandled errors")
            if data["success"] == failed:
                report.errors.append("success flag disagrees with assertions")
        except (ValueError, TypeError, KeyError, AttributeError):
            report.errors.append("malformed or incomplete Vitest JSON report")
        return _check(report, capture, failed)
