# fail-to-pass

Does a test detect the fix it accompanies? Run the head's executable tests against committed
base and head snapshots, then classify each test from repeated structured results.
Python 3.11+, Git and POSIX process groups are required. No runtime Python dependencies.
Go test JSON and Vitest JSON are supported. This tool runs code; use an isolated runner for
untrusted changes. It provides evidence, not a security sandbox.

## Quick start

From a source checkout:

```sh
python -m pip install .
fail-to-pass --base main --runner go-json --test-glob '*_test.go' -- \
  go test -json -count=1 ./...
```

The base is the merge base of the requested comparison ref and committed head. Dirty and
untracked files are ignored and left untouched. Only files matching `--test-glob` receive head
content on the base side. Use narrow globs for the tests you intend to execute. The command runs
in each exported snapshot; dependencies must already be available through the toolchain or an
explicit setup wrapper. Snapshots contain tracked regular files, without `.git` or installed packages.
Links and submodules are rejected.

Exit 0 means every selected test has confirmed assertion-level `fail-to-pass` evidence and head
reports no failures or parser errors. Exit 1 means evidence did not satisfy policy; exit 2 means
configuration or preparation failed. With no selection file, all reported head tests are selected.
Use a focused command, or `--selection` to name the tests required to prove this change.

## Guards and test-source subjects

A new guard can correctly pass on the base. Declare `--allow-pass-to-pass` for such a change;
output still says `p2p`, so this does not claim regression detection. Keep the default policy for
regression fixes: a test that always passes must be rejected.

A guard may inspect other test files. Overlay only the executable guard, and optionally declare
`--subject-glob` patterns to reject accidental runner/subject overlap. For a guard inspecting its
own tracked source, execute a head copy outside the measured trees:

```sh
fail-to-pass --base main --runner vitest-json --test-glob 'checks/guard.py' \
  --subject-glob 'checks/*.py' --external-tests -- python '{tests}'
```

Here `guard.py` is a wrapper emitting Vitest-shaped JSON on stdout, with the matching exit status.
`{tests}` must be a separate argument. It expands into relative runner paths in overlay mode,
or absolute external copies in external mode. The command's working directory remains the
measured snapshot, so the external guard must read subjects relative to that directory.

See [usage](docs/usage.md), [architecture](docs/architecture.md),
[contributing](CONTRIBUTING.md), [security](SECURITY.md) and [changes](CHANGELOG.md).
Licensed under [Apache-2.0](LICENSE).

## GitHub Action

The repository root is a composite Action, usable by release tag (or preferably an immutable
commit). Replace `YOUR_ORG` with the publishing organization after release:

```yaml
permissions:
  contents: read
steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0
      persist-credentials: false
  - uses: actions/setup-python@v5
    with:
      python-version: '3.13'
  - uses: YOUR_ORG/fail-to-pass@v0.1.0
    with:
      base: origin/main
```

Set up the project's Go/Node dependencies first. Store command and test globs in
`.fail-to-pass.toml`. Use isolated pull-request jobs without secrets; never execute a contributor's
code from a privileged `pull_request_target` workflow.
