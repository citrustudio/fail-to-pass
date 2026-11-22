# Contributing

Use Python 3.11+ and Git on a POSIX host. The runtime uses only the standard library.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m pip install --require-hashes -r requirements-ci.txt
python -m build --no-isolation
```

Go integration tests run when Go is on PATH, without module downloads. Live Vitest coverage runs
when `FAIL_TO_PASS_VITEST_MODULE` points to a local `vitest/dist/index.js`; otherwise that optional
probe skips. Recorded neutral reports and malformed-report controls always run.
Add a negative control for changes to evidence or policy. A malformed report, missing test,
compile-only red, or test that always passes must not become assertion-level regression evidence.
Keep execution and classification separate. New adapters need reporter fixtures, contradictory
exit controls, identity tests and live reporter coverage. Do not include secrets or personal paths
in reports or fixtures. Describe the problem, changed behavior and focused test result in a pull
request. Release setup and first publication are maintainer operations.
