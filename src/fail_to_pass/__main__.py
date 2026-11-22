import argparse
import json
import sys
import subprocess
from pathlib import Path
import tomllib

from . import GoJSON, Policy, TestId, VitestJSON
from .check import accepted, check
from .git_tree import snapshots


KEYS = {'base', 'head', 'runner', 'test_globs', 'subject_globs', 'command',
        'selection', 'allow_pass_to_pass', 'timeout', 'external_tests'}


def strings(value, name, required=True):
    if not isinstance(value, list) or (required and not value) or any(
            not isinstance(v, str) or not v for v in value):
        raise ValueError(name + ' must be an array of nonempty strings')
    return value


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['measure']:
        from .prepared import main as prepared_main
        return prepared_main(argv[1:])
    parser = argparse.ArgumentParser(description='Classify tests against committed base and head snapshots')
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--config', type=Path, help='defaults to .fail-to-pass.toml in --repo')
    parser.add_argument('--base', help='comparison ref; uses its merge base with head')
    parser.add_argument('--head', help='committed head ref (default HEAD)')
    parser.add_argument('--runner', choices=['go-json', 'vitest-json'])
    parser.add_argument('--test-glob', action='append', dest='test_globs')
    parser.add_argument('--subject-glob', action='append', dest='subject_globs')
    parser.add_argument('--selection', type=Path, help='JSON array of suite/name identities; default all reported head tests')
    parser.add_argument('--allow-pass-to-pass', action=argparse.BooleanOptionalAction, default=None,
                        help='accept satisfied guards; does not prove regression detection')
    parser.add_argument('--external-tests', action=argparse.BooleanOptionalAction, default=None,
                        help='execute head test copies outside both measured trees; requires {tests}')
    parser.add_argument('--timeout', type=float, help='seconds per test invocation, at most 600')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        config_path = args.config or args.repo / '.fail-to-pass.toml'
        config = {}
        if args.config or config_path.exists():
            config = tomllib.loads(config_path.read_text())
        if set(config) - KEYS:
            raise ValueError('unknown configuration keys: ' + ', '.join(sorted(set(config) - KEYS)))
        for key in KEYS - {'command'}:
            value = getattr(args, key)
            if value is not None:
                config[key] = value
        command = args.command
        if command[:1] == ['--']:
            command = command[1:]
        command = strings(command or config.get('command'), 'command')
        for key in ('base', 'head', 'runner'):
            config.setdefault(key, 'HEAD' if key == 'head' else None)
            if not isinstance(config[key], str) or not config[key]:
                raise ValueError(key + ' must be a nonempty string')
        if config['runner'] not in ('go-json', 'vitest-json'):
            raise ValueError('runner must be go-json or vitest-json')
        if config['runner'] == 'go-json' and (command[:2] != ['go', 'test'] or
                                            '-json' not in command or '-count=1' not in command):
            raise ValueError('Go requires go test -json -count=1')
        tests = strings(config.get('test_globs'), 'test_globs')
        subjects = strings(config.get('subject_globs', []), 'subject_globs', required=False)
        for pattern in tests + subjects:
            if pattern.startswith('/') or '..' in pattern.split('/'):
                raise ValueError('globs must be repository-relative without parent traversal')
        external = config.get('external_tests', False)
        if type(external) is not bool or (external and '{tests}' not in command):
            raise ValueError('external_tests requires a boolean and a {tests} command argument')
        allow = config.get('allow_pass_to_pass', False)
        if type(allow) is not bool:
            raise ValueError('allow_pass_to_pass must be a boolean')
        timeout = config.get('timeout', 600)
        if type(timeout) not in (float, int):
            raise ValueError('timeout must be numeric')
        policy = Policy(timeout=timeout)
        selection = config.get('selection')
        if selection is not None:
            # Config-relative selection paths make invocation independent of the shell cwd.
            path = selection if isinstance(selection, Path) else config_path.parent / selection
            rows = json.loads(path.read_text())
            if not isinstance(rows, list) or not rows:
                raise ValueError('selection must be a nonempty JSON array')
            selection = {TestId(**row) for row in rows}
        with snapshots(args.repo, config['base'], config['head'], tests, subjects, external) as (base, head, provenance):
            # {tests} expands into separate relative path arguments, never shell text.
            expanded = [part for arg in command for part in
                        (provenance['_arguments'] if arg == '{tests}' else [arg])]
            adapter = GoJSON() if config['runner'] == 'go-json' else VitestJSON()
            result = check(adapter, expanded, base, head, selection, policy)
            ok = accepted(result, allow)
            provenance.pop('_arguments')
            payload = result.as_dict()
            payload.update({'git': provenance, 'accepted': ok,
                            'policy': {'allow_pass_to_pass': allow}})
            print(json.dumps(payload, indent=2))
            return 0 if ok else 1
    except (ValueError, TypeError, OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, 'fail-to-pass: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
