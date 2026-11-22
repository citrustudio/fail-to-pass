import argparse
import json
from pathlib import Path

from . import GoJSON, Outcome, RunSpec, Status, TestId, VitestJSON, measure


def main(argv=None):
    parser = argparse.ArgumentParser(description="Classify selected tests in two prepared trees")
    parser.add_argument("--runner", choices=["go-json", "vitest-json"], required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True,
                        help='JSON array of {"suite": ..., "name": ...}')
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("provide a structured-reporter command after --")
    if args.runner == "go-json" and (command[:2] != ["go", "test"] or
                                    "-json" not in command or "-count=1" not in command):
        parser.error("Go requires go test -json -count=1")
    try:
        selected = {TestId(**row) for row in json.loads(args.selection.read_text())}
        result = measure(GoJSON() if args.runner == "go-json" else VitestJSON(),
                         RunSpec(command, args.base), RunSpec(command, args.head), selected)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result.as_dict(), indent=2))
    hard = {Outcome.PASS_TO_FAIL, Outcome.FAIL_TO_FAIL, Outcome.UNJUDGED, Outcome.FLAKY}
    head_failed = any(report.compile_suites or Status.FAIL in report.tests.values()
                      for report in result.head)
    return int(head_failed or any(r.outcome in hard for r in result.results) or
               not any(r.outcome == Outcome.FAIL_TO_PASS for r in result.results))


if __name__ == "__main__":
    raise SystemExit(main())
