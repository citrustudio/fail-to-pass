"""Committed snapshots with an explicit executable-test overlay."""
from contextlib import contextmanager
import fnmatch
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


def git(repo, *args, data=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=60, env=env, input=data)
    if result.returncode:
        raise ValueError('Git operation failed: ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def resolve(repo, ref):
    return git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def export(repo, sha, target):
    # Read blobs directly: archive attributes can omit or substitute committed bytes.
    entries = []
    for entry in filter(None, git(repo, 'ls-tree', '-rz', sha).split(b'\0')):
        metadata, name = entry.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        path = PurePosixPath(os.fsdecode(name))
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise ValueError('snapshots require regular files (no links or submodules)')
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('unsafe snapshot entry')
        entries.append((mode, oid, path))
    blobs = io.BytesIO(git(repo, 'cat-file', '--batch',
                          data=b''.join(oid + b'\n' for _, oid, _ in entries)))
    for mode, oid, path in entries:
        actual, kind, size = blobs.readline().split()
        if actual != oid or kind != b'blob':
            raise ValueError('unexpected Git object')
        content = blobs.read(int(size))
        if len(content) != int(size) or blobs.read(1) != b'\n':
            raise ValueError('truncated Git object')
        destination = target.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o755 if mode == b'100755' else 0o644)


@contextmanager
def snapshots(repo: Path, base_ref: str, head_ref: str, test_globs, subject_globs=(), external=False):
    head_sha, requested_base = resolve(repo, head_ref), resolve(repo, base_ref)
    base_sha = git(repo, 'merge-base', requested_base, head_sha).decode().strip()
    with tempfile.TemporaryDirectory(prefix='fail-to-pass-') as directory:
        root = Path(directory)
        base, head = root / 'base', root / 'head'
        base.mkdir(); head.mkdir()
        export(repo, base_sha, base)
        export(repo, head_sha, head)
        files = sorted(p.relative_to(head).as_posix() for p in head.rglob('*') if p.is_file())
        tests = [p for p in files if matches(p, test_globs)]
        if any(not any(matches(p, [pattern]) for p in files) for pattern in test_globs):
            raise ValueError('a test glob matched no head files')
        if not external and any(matches(p, subject_globs) for p in tests):
            raise ValueError('executable test and subject globs overlap')
        checks = root / 'checks'
        for name in tests:
            destination = (checks if external else base) / name
            # An old directory/file collision is not valid executable-test evidence.
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((head / name).read_bytes())
            destination.chmod((head / name).stat().st_mode & 0o777)
        yield base, head, {'base': base_sha, 'head': head_sha,
                           'overlay': [] if external else tests, 'tests': tests,
                           'external_tests': external,
                           '_arguments': [str(checks / p) for p in tests] if external else tests}
