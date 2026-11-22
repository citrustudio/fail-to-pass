# Usage

CLI options override matching config values; repeated CLI globs replace the config glob array.
The default config is `.fail-to-pass.toml` in `--repo` (default current directory). Explicit
`--config` and CLI `--selection` paths are relative to the invocation directory; a config selection
path is relative to its config file. Unknown keys and malformed values are errors.

```toml
base = "origin/main"
head = "HEAD"
runner = "go-json"
test_globs = ["*_test.go"]
subject_globs = []
command = ["go", "test", "-json", "-count=1", "./..."]
timeout = 120
allow_pass_to_pass = false
external_tests = false
# selection = "selection.json"
```

Globs use case-sensitive Python `fnmatch` over repository-relative POSIX paths: `*` includes
slashes. Every executable-test glob must match at least one head file. For a test-source guard,
use `subject_globs` to assert that its inspected files are not accidentally overlaid. External mode
permits overlap because no executable test is written into the base. `{tests}` expands to one
argument per matching path and is mandatory for external mode. Commands are argv arrays, with no
implicit shell and no string interpolation. A wrapper is appropriate for reporters needing an
output file; it must emit only the fresh report on stdout and logs on stderr.

For Vitest use `vitest-json` and a command such as
`["vitest", "run", "--reporter=json", "--retry=0", "{tests}"]`, with the executable available on
PATH and dependencies prepared for both snapshots. Disable runner retries. Go must use
`go test -json -count=1` to avoid cached results. The default timeout is 600 seconds per
invocation, the maximum allowed. At most four base runs and exactly two head runs occur.
Assertion or compile red on the first base run triggers three additional base runs.
No repeated red or missing terminal results can be assumed to be good evidence.

An optional selection JSON array names identities:

```json
[{"suite": "example.test/math", "name": "TestSum"}]
```

Go suite identity is the import path; names include subtest paths. Vitest identity is the file
relative to the invocation directory plus `fullName`. In external mode, a custom reporter must
emit identities relative to the measured tree (for example `checks/guard.py`), not the external
execution path. Missing selections are unjudged. Explicit selection allows other tests to remain
p2p; all reported head failures, including unselected failures, still reject the run.

## Outcome classes

| Class | Meaning |
|---|---|
| `fail-to-pass` | Confirmed assertion red on base, repeated green on head |
| `compile-only-red` | Base package cannot compile, head green; not proof of behavior |
| `skip-to-pass` | Base skips, head green; not assertion red |
| `p2p` | Both pass; accepted only with explicit guard policy |
| `pass-to-fail` | Base passes, head fails |
| `f2f` | Head fails and base did not pass |
| `flaky` | Observed statuses differ between repetitions on one side |
| `unjudged` | Missing, malformed, timed-out, inconsistent or insufficient evidence |

JSON includes per-test classes and status arrays, parsed run reports, accepted policy, exact base
and head commit IDs, executable files and overlay mode. Finite repetition detects observed flakes,
not every possible flake. Compile errors are distinguished from assertion failures only where
reporter evidence supports it. Vitest load errors are unjudged. Go build errors apply only to the
reported package. A crash without a terminal result is unjudged.

## Prepared trees and library

For callers that already prepare environments, the original interface is retained explicitly:

```sh
fail-to-pass measure --runner go-json --base ./base --head ./head \
  --selection selection.json -- go test -json -count=1 ./...
```

This mode requires at least one selected fail-to-pass and rejects selected flaky, unjudged or
failing tests and reported head failures. Compile-only and skip-to-pass classes are diagnostic and
do not independently satisfy the required red. It has no Git preparation or p2p guard policy.

The library exports `measure(adapter, RunSpec(command, cwd), RunSpec(command, cwd), selection)`;
`GoJSON` and `VitestJSON` implement `parse(Capture, cwd) -> Report`. `Policy` permits one to three
additional base runs and exactly two head runs, with a finite timeout of at most 600 seconds.
