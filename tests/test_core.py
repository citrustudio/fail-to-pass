import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fail_to_pass import (Capture, GoJSON, Outcome, Policy, Report, RunSpec, Status,
                          TestId, VitestJSON, classify, execute, measure)


FIXTURES = Path(__file__).parent / "fixtures"


def scrubbed_env(*, drop=()):
    # Fixture subprocesses must not inherit a caller's Git repository or identity.
    return {k: v for k, v in os.environ.items()
            if k not in drop and not k.startswith(("GIT_", "AGENT_", "SUITE_SLOT_"))}


TEST = TestId("example.test/math", "TestSum")


def report(status):
    return Report({TEST: status})


class ClassificationTests(unittest.TestCase):
    def test_all_eight_classes_and_skip_head(self):
        cases = [(Status.FAIL, Status.PASS, Outcome.FAIL_TO_PASS),
                 (Status.COMPILE, Status.PASS, Outcome.COMPILE_ONLY),
                 (Status.SKIP, Status.PASS, Outcome.SKIP_TO_PASS),
                 (Status.PASS, Status.FAIL, Outcome.PASS_TO_FAIL),
                 (Status.PASS, Status.PASS, Outcome.PASS_TO_PASS),
                 (Status.FAIL, Status.FAIL, Outcome.FAIL_TO_FAIL),
                 (Status.PASS, Status.COMPILE, Outcome.PASS_TO_FAIL),
                 (Status.FAIL, Status.COMPILE, Outcome.FAIL_TO_FAIL),
                 (Status.UNKNOWN, Status.PASS, Outcome.UNJUDGED),
                 (Status.PASS, Status.SKIP, Outcome.UNJUDGED)]
        for base, head, expected in cases:
            with self.subTest(base=base, head=head):
                self.assertEqual(classify(TEST, [report(base)] * 4,
                                          [report(head)] * 2).outcome, expected)
        self.assertEqual(classify(TEST, [report(Status.FAIL), report(Status.PASS)],
                                  [report(Status.PASS)] * 2).outcome, Outcome.FLAKY)
        self.assertEqual(classify(TEST, [report(Status.FAIL)] * 4,
                                  [report(Status.PASS), report(Status.FAIL)]).outcome, Outcome.FLAKY)

    def test_every_status_combination_has_exactly_one_class(self):
        for b1, b2, h1, h2 in itertools.product(Status, repeat=4):
            result = classify(TEST, [report(b1), report(b2)], [report(h1), report(h2)])
            self.assertIsInstance(result.outcome, Outcome)
            if Status.UNKNOWN in (b1, b2, h1, h2):
                self.assertEqual(result.outcome, Outcome.UNJUDGED)

    def test_missing_test_cannot_borrow_another_suites_compile_error(self):
        base = Report(compile_suites={"other"})
        self.assertEqual(classify(TEST, [base], [report(Status.PASS)] * 2).outcome,
                         Outcome.UNJUDGED)

    def test_missing_repeated_head_evidence(self):
        self.assertEqual(classify(TEST, [report(Status.FAIL)], [report(Status.PASS)]).outcome,
                         Outcome.UNJUDGED)

    def test_red_classification_requires_repeated_base_evidence(self):
        for status, expected in ((Status.FAIL, Outcome.FAIL_TO_PASS),
                                 (Status.COMPILE, Outcome.COMPILE_ONLY)):
            with self.subTest(status=status):
                self.assertEqual(classify(TEST, [report(status)], [report(Status.PASS)] * 2).outcome,
                                 Outcome.UNJUDGED)
                self.assertEqual(classify(TEST, [report(status)] * 2, [report(Status.PASS)] * 2).outcome,
                                 expected)

    def test_reruns_bounded_and_selected_partition(self):
        calls = []
        class Adapter:
            def parse(self, cap, cwd):
                return report(Status.FAIL if cap.returncode else Status.PASS)
        def runner(spec, timeout):
            calls.append((spec.cwd, timeout))
            return Capture("", "", int(spec.cwd == Path("base")))
        result = measure(Adapter(), RunSpec([], Path("base")), RunSpec([], Path("head")),
                         {TEST}, runner=runner)
        self.assertEqual([p for p, _ in calls], [Path("base")] * 4 + [Path("head")] * 2)
        self.assertEqual({t for _, t in calls}, {600})
        self.assertEqual([r.test for r in result.results], [TEST])
        self.assertEqual(result.results[0].outcome, Outcome.FAIL_TO_PASS)
        calls.clear()
        measure(Adapter(), RunSpec([], Path("head")), RunSpec([], Path("head")),
                {TEST}, runner=runner)
        self.assertEqual(len(calls), 3)

    def test_invalid_policy_and_empty_selection(self):
        for kwargs in ({"retries_base": 0}, {"retries_base": 4}, {"retries_base": 1.5},
                       {"repeats_head": 1}, {"repeats_head": 2.0},
                       {"timeout": 601}, {"timeout": 0}, {"timeout": float('nan')}):
            with self.assertRaises(ValueError):
                Policy(**kwargs)
        with self.assertRaises(ValueError):
            measure(GoJSON(), RunSpec([], Path()), RunSpec([], Path()), set())


class AdapterTests(unittest.TestCase):
    def test_recorded_neutral_reporters(self):
        cases = [(GoJSON(), "recorded-go.jsonl", "example.test/math",
                  {"TestSum": Status.PASS, "TestBroken": Status.FAIL, "TestLater": Status.SKIP}),
                 (VitestJSON(), "recorded-vitest.json", "sum.test.mjs",
                  {"math adds": Status.PASS, "math subtracts": Status.FAIL,
                   "math later": Status.SKIP})]
        for adapter, fixture, suite, expected in cases:
            with self.subTest(fixture=fixture):
                parsed = adapter.parse(Capture((FIXTURES / fixture).read_text(), "", 1), Path())
                self.assertFalse(parsed.errors)
                self.assertFalse(parsed.compile_suites)
                self.assertEqual(parsed.tests, {TestId(suite, name): status
                                                for name, status in expected.items()})

    def test_go_control_with_duplicate_names_across_packages(self):
        r = GoJSON().parse(Capture((FIXTURES / "go.jsonl").read_text(), "", 1), Path())
        self.assertFalse(r.errors)
        self.assertEqual(r.tests[TEST], Status.PASS)
        self.assertEqual(r.tests[TestId("example.test/other", "TestSum")], Status.SKIP)

    def test_go_build_only_and_scope(self):
        for events in ([{"Action": "build-fail", "ImportPath": TEST.suite}],
                       [{"Action": "fail", "Package": TEST.suite, "FailedBuild": "dependency"}],
                       [{"Action": "output", "Package": TEST.suite,
                         "Output": "FAIL\texample.test/math [build failed]"},
                        {"Action": "fail", "Package": TEST.suite}]):
            r = GoJSON().parse(Capture("\n".join(map(json.dumps, events)), "", 1), Path())
            self.assertEqual(r.status(TEST), Status.COMPILE)

    def test_go_crash_missing_terminal_and_spoofed_exit(self):
        for text, rc in [('', 0), ('garbage', 1),
                         (json.dumps({"Action": "run", "Package": TEST.suite, "Test": TEST.name}), 1),
                         ((FIXTURES / "go.jsonl").read_text(), 0),
                         (json.dumps({"Action": "fail", "Package": TEST.suite}), 1)]:
            with self.subTest(text=text[:30]):
                self.assertEqual(GoJSON().parse(Capture(text, "", rc), Path()).status(TEST),
                                 Status.UNKNOWN)

    def test_vitest_control(self):
        r = VitestJSON().parse(Capture((FIXTURES / "vitest.json").read_text(), "", 1), Path())
        self.assertFalse(r.errors)
        self.assertEqual(len(r.tests), 3)
        self.assertEqual(r.tests[TestId("sum.test.ts", "math later")], Status.SKIP)

    def test_vitest_no_report_crash_and_exit_disagreement(self):
        fixture = (FIXTURES / "vitest.json").read_text()
        for text, rc in [("", 1), ("Worker exited unexpectedly", 1), ("{}", 0),
                         (fixture, 0), (fixture, -9)]:
            self.assertTrue(VitestJSON().parse(Capture(text, "", rc), Path()).errors)

    def test_vitest_nonterminal_assertions_cannot_confirm_red(self):
        data = json.loads((FIXTURES / "vitest.json").read_text())
        data['testResults'][0]['assertionResults'][-1]['status'] = 'pending'
        r = VitestJSON().parse(Capture(json.dumps(data), "", 1), Path())
        self.assertTrue(r.errors)
        broken = TestId("sum.test.ts", "math subtracts")
        self.assertEqual(classify(broken, [r] * 4, [Report({broken: Status.PASS})] * 2).outcome,
                         Outcome.UNJUDGED)

    def test_go_malformed_identities_are_unjudged_and_serializable(self):
        valid = (FIXTURES / "go.jsonl").read_text()
        build = json.dumps({"Action": "build-fail", "ImportPath": "example.test/build"})
        broken = TestId(TEST.suite, "TestBroken")
        bad_events = [{}, {"Action": 1, "Package": TEST.suite},
                      {"Action": "fail", "Package": TEST.suite, "FailedBuild": True}]
        bad_events += [{"Action": action, "Package": TEST.suite} for action in ("run", "pause", "cont")]
        bad_events += [{"Action": "build-fail", "ImportPath": value}
                       for value in (None, 1, [], {}, " ")]
        for bad in bad_events:
            with self.subTest(event=bad):
                r = GoJSON().parse(Capture(valid + '\n' + build + '\n' + json.dumps(bad), "", 1), Path())
                self.assertTrue(r.errors)
                self.assertEqual(classify(broken, [r], [Report({broken: Status.PASS})] * 2).outcome,
                                 Outcome.UNJUDGED)
                json.dumps(r.as_dict())

    def test_go_conflicting_suite_terminals_cannot_confirm_red(self):
        events = [
            {"Action": "run", "Package": TEST.suite, "Test": TEST.name},
            {"Action": "fail", "Package": TEST.suite, "Test": TEST.name},
            {"Action": "pass", "Package": TEST.suite},
            {"Action": "fail", "Package": TEST.suite},
        ]
        for terminal in ("pass", "fail", "skip"):
            with self.subTest(terminal=terminal):
                events[-2]["Action"] = terminal
                r = GoJSON().parse(Capture('\n'.join(map(json.dumps, events)), "", 1), Path())
                self.assertTrue(r.errors)
                self.assertEqual(classify(TEST, [r] * 4, [report(Status.PASS)] * 2).outcome,
                                 Outcome.UNJUDGED)

    def test_go_test_suite_and_exit_terminals_must_agree(self):
        valid = {("pass", "pass", 0), ("skip", "pass", 0),
                 ("skip", "skip", 0), ("fail", "fail", 1)}
        for test_status in ("pass", "fail", "skip"):
            for suite_status in ("pass", "fail", "skip"):
                for rc in (0, 1):
                    case = (test_status, suite_status, rc)
                    with self.subTest(case=case):
                        events = [
                            {"Action": "run", "Package": TEST.suite, "Test": TEST.name},
                            {"Action": test_status, "Package": TEST.suite, "Test": TEST.name},
                            {"Action": suite_status, "Package": TEST.suite},
                        ]
                        r = GoJSON().parse(Capture('\n'.join(map(json.dumps, events)), "", rc), Path())
                        if case in valid:
                            self.assertFalse(r.errors)
                            self.assertEqual(r.status(TEST), Status(test_status))
                        else:
                            self.assertTrue(r.errors)
                            self.assertEqual(r.status(TEST), Status.UNKNOWN)
        for terminal in ("pass", "skip"):
            with self.subTest(build_terminal=terminal):
                events = [{"Action": "build-fail", "ImportPath": TEST.suite},
                          {"Action": terminal, "Package": TEST.suite}]
                r = GoJSON().parse(Capture('\n'.join(map(json.dumps, events)), "", 1), Path())
                self.assertEqual(r.status(TEST), Status.UNKNOWN)

    def test_vitest_malformed_identities_are_unjudged_and_serializable(self):
        broken = TestId("sum.test.ts", "math subtracts")
        for value in (None, 1, [], {}, "", ".", "sub/.."):
            with self.subTest(name=value):
                data = json.loads((FIXTURES / "vitest.json").read_text())
                data['testResults'][0]['name'] = value
                r = VitestJSON().parse(Capture(json.dumps(data), "", 1), Path())
                self.assertTrue(r.errors)
                self.assertEqual(classify(broken, [r], [Report({broken: Status.PASS})] * 2).outcome,
                                 Outcome.UNJUDGED)
                json.dumps(r.as_dict())

    def test_vitest_duplicate_identity_and_runtime_error(self):
        data = json.loads((FIXTURES / "vitest.json").read_text())
        data['testResults'][0]['assertionResults'] *= 2
        self.assertTrue(VitestJSON().parse(Capture(json.dumps(data), "", 1), Path()).errors)
        data['testResults'] = []
        data['numRuntimeErrorTestSuites'] = 1
        self.assertTrue(VitestJSON().parse(Capture(json.dumps(data), "", 1), Path()).errors)

    def test_timeout_overrides_even_a_valid_report(self):
        r = GoJSON().parse(Capture((FIXTURES / "go.jsonl").read_text(), "", 1, True), Path())
        self.assertEqual(r.status(TEST), Status.UNKNOWN)


class ExecutionTests(unittest.TestCase):
    def test_cli_cannot_ignore_an_unselected_head_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for side in ('base', 'head'):
                (root / side).mkdir()
            selection = root / 'selection.json'
            selection.write_text(json.dumps([{'suite': 'sum.test.ts', 'name': 'adds'}]))
            script = root / 'reporter.py'
            script.write_text('import json\nfrom pathlib import Path\n'
                'base = Path.cwd().name == "base"\n'
                'print(json.dumps({"success": False, "testResults": [{'
                '"name": "sum.test.ts", "status": "failed", "assertionResults": ['
                '{"fullName": "adds", "status": "failed" if base else "passed"}, '
                '{"fullName": "other", "status": "passed" if base else "failed"}]}]}))\n'
                'raise SystemExit(1)\n')
            cap = subprocess.run([sys.executable, '-m', 'fail_to_pass', 'measure', '--runner', 'vitest-json',
                '--base', str(root / 'base'), '--head', str(root / 'head'),
                '--selection', str(selection), '--', sys.executable, str(script)],
                capture_output=True, text=True)
            self.assertEqual(cap.returncode, 1, cap.stderr)
            self.assertEqual(json.loads(cap.stdout)['results'][0]['class'], 'fail-to-pass')

    def test_cli_emits_partition_and_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for side in ('base', 'head'):
                (root / side).mkdir()
            selection = root / 'selection.json'
            selection.write_text(json.dumps([{'suite': 'sum.test.ts', 'name': 'adds'}]))
            script = root / 'reporter.py'
            script.write_text('import json\nfrom pathlib import Path\n'
                'failed = Path.cwd().name == "base"\n'
                'print(json.dumps({"success": not failed, "testResults": [{'
                '"name": "sum.test.ts", "status": "failed" if failed else "passed", '
                '"assertionResults": [{"fullName": "adds", '
                '"status": "failed" if failed else "passed"}]}]}))\n'
                'raise SystemExit(int(failed))\n')
            command = [sys.executable, '-m', 'fail_to_pass', 'measure', '--runner', 'vitest-json',
                       '--base', str(root / 'base'), '--head', str(root / 'head'),
                       '--selection', str(selection), '--', sys.executable, str(script)]
            cap = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(cap.returncode, 0, cap.stderr)
            self.assertEqual(json.loads(cap.stdout)['results'][0]['class'], 'fail-to-pass')
            selection.write_text(json.dumps([{'suite': 'missing.test.ts', 'name': 'adds'}]))
            cap = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(cap.returncode, 1, cap.stderr)
            self.assertEqual(json.loads(cap.stdout)['results'][0]['class'], 'unjudged')

    def test_timeout_and_missing_executable(self):
        cap = execute(RunSpec([sys.executable, "-c", "import time; time.sleep(10)"], Path()), 0.05)
        self.assertTrue(cap.timed_out)
        self.assertNotEqual(cap.returncode, 0)
        self.assertEqual(execute(RunSpec(["/nonexistent-executable"], Path()), 1).returncode, 127)

    @unittest.skipUnless(shutil.which("go") and shutil.which("git"), "Go or Git is not installed")
    def test_live_go_pass_assertion_compile_and_seeded_flake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture_env = scrubbed_env(
                drop=subprocess.check_output(['git', 'rev-parse', '--local-env-vars'], text=True).splitlines())
            subprocess.run(['git', 'init', '-q', str(root)], env=fixture_env, check=True,
                           capture_output=True)
            base, head = root / "base", root / "head"
            for path in (base, head):
                path.mkdir()
                (path / 'go.mod').write_text('module example.test/math\n\ngo 1.20\n')
                (path / 'sum_test.go').write_text('package math\nimport "testing"\n'
                    'func TestSum(t *testing.T) { if Sum() != 2 { t.Fatal("wrong sum") } }\n')
            (base / 'sum.go').write_text('package math\nfunc Sum() int { return 1 }\n')
            (head / 'sum.go').write_text('package math\nfunc Sum() int { return 2 }\n')
            cmd = ['go', 'test', '-json', '-count=1', './...']
            def run():
                return measure(GoJSON(), RunSpec(cmd, base), RunSpec(cmd, head), {TEST})
            self.assertEqual(run().results[0].outcome, Outcome.FAIL_TO_PASS)
            (base / 'sum.go').write_text('package math\n')
            self.assertEqual(run().results[0].outcome, Outcome.COMPILE_ONLY)
            (base / 'sum_test.go').write_text('package math\nimport ("testing";"os")\n'
                'func TestSum(t *testing.T) { _, err := os.Stat("counter"); '
                'os.WriteFile("counter", []byte("1"), 0600); if err != nil { t.Fatal("first run") } }\n')
            (head / 'sum_test.go').write_text((base / 'sum_test.go').read_text())
            self.assertEqual(run().results[0].outcome, Outcome.FLAKY)

    def test_live_vitest_if_local_dependencies_are_available(self):
        module = os.environ.get("FAIL_TO_PASS_VITEST_MODULE")
        if not module:
            candidates = [Path.cwd() / "node_modules/vitest/dist/index.js",
                          Path(__file__).resolve().parents[1] / "node_modules/vitest/dist/index.js"]
            module = next((str(p) for p in candidates if p.exists()), None)
        if not module or not Path(module).exists():
            self.skipTest("local Vitest module absent; set FAIL_TO_PASS_VITEST_MODULE")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "sample.test.mjs").write_text(
                'import { test, expect } from ' + json.dumps(Path(module).as_uri()) + ';\n'
                'test("adds", () => expect(1 + 1).toBe(2));\n')
            cli = Path(module).parent.parent / "vitest.mjs"
            cap = execute(RunSpec(["node", str(cli), "run", "--reporter=json", "--retry=0"], root), 60)
            r = VitestJSON().parse(cap, root)
            self.assertFalse(r.errors, (r.errors, cap.stderr, cap.stdout[:500]))
            self.assertEqual(r.tests[TestId("sample.test.mjs", "adds")], Status.PASS)


if __name__ == "__main__":
    unittest.main()
