import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fail_to_pass.git_tree import snapshots


SOURCE = str(Path(__file__).resolve().parents[1] / 'src')


def scrubbed_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('GIT_', 'AGENT_', 'SUITE_SLOT_'))}
    local_vars = subprocess.check_output(['git', 'rev-parse', '--local-env-vars'], env=env, text=True)
    for name in local_vars.splitlines():
        env.pop(name, None)
    return env


# A real assertion with structured output, independent of a JavaScript install.
GUARD = '''import json
from pathlib import Path
bad = "FORBIDDEN" in Path("subject.test.txt").read_text()
print(json.dumps({"success": not bad, "testResults": [{"name": "guard.py",
"status": "failed" if bad else "passed", "assertionResults": [
{"fullName": "guard", "status": "failed" if bad else "passed"}]}]}))
raise SystemExit(int(bad))
'''


class GitCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.env = scrubbed_env()
        self.env.update(PYTHONPATH=SOURCE, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@users.noreply.github.com')
        self.write('subject.test.txt', 'FORBIDDEN')
        self.write('guard.py', GUARD)
        self.commit()
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.write('subject.test.txt', 'clean')
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), '-c', 'core.hooksPath=/dev/null',
                                        *args], env=self.env, text=True, stderr=subprocess.PIPE)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')

    def cli(self, *options, command=None):
        return subprocess.run([sys.executable, '-m', 'fail_to_pass', '--repo', str(self.repo),
                               '--base', self.base, '--runner', 'vitest-json',
                               '--test-glob', 'guard.py', *options, '--',
                               *(command or [sys.executable, '{tests}'])],
                              env=self.env, text=True, capture_output=True)

    def result(self, cap, rc=0, outcome='fail-to-pass'):
        self.assertEqual(cap.returncode, rc, cap.stderr)
        data = json.loads(cap.stdout)
        self.assertEqual(data['results'][0]['class'], outcome)
        self.assertEqual(data['accepted'], rc == 0)
        return data

    def test_guard_scanning_test_sources_and_caller_stays_dirty(self):
        self.write('untracked', 'leave me')
        self.write('subject.test.txt', 'uncommitted content')
        before = self.git('status', '--porcelain')
        data = self.result(self.cli('--subject-glob', '*.test.txt'))
        self.assertEqual(data['git']['overlay'], ['guard.py'])
        self.assertEqual(data['git']['base'], self.base)
        self.assertEqual(self.git('status', '--porcelain'), before)
        self.assertEqual((self.repo / 'subject.test.txt').read_text(), 'uncommitted content')

    def test_negative_control_overlaying_subject_hides_the_red(self):
        self.result(self.cli('--test-glob', '*.test.txt',
                            command=[sys.executable, 'guard.py']), rc=1, outcome='p2p')
        cap = self.cli('--test-glob', '*.test.txt', '--subject-glob', '*.test.txt')
        self.assertEqual(cap.returncode, 2)
        self.assertIn('overlap', cap.stderr)

    def test_new_satisfied_guard_is_explicit_p2p_not_a_claimed_regression(self):
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.write('new_guard.py', GUARD)
        self.commit()
        self.result(self.cli('--test-glob', 'new_guard.py',
                             command=[sys.executable, 'new_guard.py']), rc=1, outcome='p2p')
        self.result(self.cli('--test-glob', 'new_guard.py', '--allow-pass-to-pass',
                             command=[sys.executable, 'new_guard.py']), outcome='p2p')

    def test_external_guard_can_scan_its_own_base_source(self):
        # New head guard inspects the tracked counterpart, not its executable copy.
        self.write('guard.py', GUARD + '\n# LEGACY\n')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD').strip()
        guard = GUARD.replace('bad = "FORBIDDEN" in Path("subject.test.txt").read_text()',
                              'bad = ("LEG" + "ACY") in Path("guard.py").read_text()')
        self.write('guard.py', guard)
        self.commit()
        data = self.result(self.cli('--external-tests', '--subject-glob', '*.py'))
        self.assertTrue(data['git']['external_tests'])
        self.assertEqual(data['git']['overlay'], [])
        cap = self.cli('--external-tests', command=[sys.executable, 'guard.py'])
        self.assertEqual(cap.returncode, 2)

    def test_config_and_explicit_selection(self):
        self.write('selection.json', json.dumps([{'suite': 'guard.py', 'name': 'guard'}]))
        self.write('.fail-to-pass.toml', 'base = ' + json.dumps(self.base) + '\n'
                   'runner = "vitest-json"\ntest_globs = ["guard.py"]\n'
                   'selection = "selection.json"\ncommand = [' + json.dumps(sys.executable) + ', "{tests}"]\n')
        cap = subprocess.run([sys.executable, '-m', 'fail_to_pass', '--repo', str(self.repo)],
                             env=self.env, text=True, capture_output=True)
        self.result(cap)
        self.write('selection.json', '[{"suite":"missing", "name":"guard"}]')
        self.result(self.cli('--selection', str(self.repo / 'selection.json')), rc=1, outcome='unjudged')

    def test_merge_base_ignores_newer_target_branch_content(self):
        self.git('branch', 'candidate')
        self.git('checkout', '-q', '--detach', self.base)
        self.write('target-only.txt', 'not the candidate base')
        self.commit()
        target = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', '-q', 'candidate')
        with snapshots(self.repo, target, 'HEAD', ['guard.py']) as (base, head, data):
            self.assertEqual(data['base'], self.base)
            self.assertFalse((base / 'target-only.txt').exists())
            self.assertFalse((head / 'target-only.txt').exists())
        self.assertFalse(base.exists())

    def test_snapshot_bytes_ignore_archive_export_attributes(self):
        self.write('.gitattributes', 'subject.test.txt export-ignore\nguard.py export-subst\n')
        self.write('guard.py', GUARD + '\n# $Format:%H$\n')
        self.commit()
        with snapshots(self.repo, self.base, 'HEAD', ['guard.py']) as (base, head, _):
            self.assertEqual((base / 'subject.test.txt').read_text(), 'FORBIDDEN')
            self.assertEqual((head / 'subject.test.txt').read_text(), 'clean')
            self.assertIn('$Format:%H$', (base / 'guard.py').read_text())

    def test_bad_configuration_and_empty_globs_fail_closed(self):
        cases = [('--test-glob', 'missing*'), ('--test-glob', '../*'),
                 ('--timeout', 'nan'), ('--base', '--help')]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 2)
        self.write('.fail-to-pass.toml', 'allow_pass_to_pass = "yes"')
        self.assertEqual(self.cli().returncode, 2)
        self.write('.fail-to-pass.toml', 'unknown = true')
        self.assertEqual(self.cli().returncode, 2)

    def test_symlink_and_submodule_entries_are_not_followed(self):
        os.symlink('subject.test.txt', self.repo / 'link')
        self.commit()
        self.assertEqual(self.cli().returncode, 2)
        self.git('rm', '-q', 'link')
        self.commit()
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.base + ',submodule')
        self.git('commit', '-qm', 'submodule')
        self.assertEqual(self.cli().returncode, 2)

    @unittest.skipUnless(shutil.which('go'), 'Go is not installed')
    def test_live_go_git_regression_and_weak_test_negative_control(self):
        self.write('go.mod', 'module example.test/math\n\ngo 1.20\n')
        self.write('sum.go', 'package math\nfunc Sum() int { return 1 }\n')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.write('sum.go', 'package math\nfunc Sum() int { return 2 }\n')
        self.write('sum_test.go', 'package math\nimport "testing"\n'
                   'func TestSum(t *testing.T) { if Sum() != 2 { t.Fatal("wrong sum") } }\n')
        self.commit()
        cmd = ['go', 'test', '-json', '-count=1', './...']
        args = ('--runner', 'go-json', '--test-glob', '*_test.go')
        # Exclude the generic Python fixture runner from this Go-only overlay.
        self.result(subprocess.run([sys.executable, '-m', 'fail_to_pass', '--repo', str(self.repo),
                    '--base', self.base, *args, '--', *cmd], env=self.env, text=True, capture_output=True))
        self.write('sum_test.go', 'package math\nimport "testing"\nfunc TestSum(t *testing.T) {}\n')
        self.commit()
        self.result(subprocess.run([sys.executable, '-m', 'fail_to_pass', '--repo', str(self.repo),
                    '--base', self.base, *args, '--', *cmd], env=self.env, text=True, capture_output=True),
                    rc=1, outcome='p2p')


if __name__ == '__main__':
    unittest.main()
