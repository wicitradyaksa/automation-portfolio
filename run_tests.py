"""Run every Python test suite in the repository.

    py run_tests.py           all three suites, then the n8n workflow linter
    py run_tests.py -v        verbose
    py run_tests.py 8 9       only projects 8 and 9

Each project is a self-contained package with its own ``tests/`` directory and
no shared conftest, so each suite runs in its own subprocess with that
project as the working directory. Importing them all into one process would
mean three packages called ``tests`` fighting over the same module name.

Everything here is standard library only. There is nothing to install, which
is the only reason a reviewer will actually run it.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent

SUITES = [
    ("8", "project-8-integration-kit", "Integration Kit"),
    ("9", "project-9-observability-layer", "Observability Layer"),
    ("10", "project-10-migration-case-study", "Migration Case Study"),
]


def run(directory: pathlib.Path, verbose: bool) -> tuple[bool, int, str]:
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    if verbose:
        command.append("-v")
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
    # unittest writes its summary to stderr, including the "Ran N tests" line.
    output = result.stderr + result.stdout
    count = 0
    for line in output.splitlines():
        if line.startswith("Ran ") and " test" in line:
            try:
                count = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    return result.returncode == 0, count, output


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    wanted = [a for a in argv if a.isdigit()]

    suites = [s for s in SUITES if not wanted or s[0] in wanted]
    if not suites:
        print("no matching suites; known: " + ", ".join(s[0] for s in SUITES), file=sys.stderr)
        return 2

    print()
    print("Running %d suite(s)" % len(suites))
    print("=" * 62)

    failures = []
    total = 0
    started = time.monotonic()

    for number, folder, label in suites:
        directory = ROOT / folder
        if not (directory / "tests").is_dir():
            print("  %-26s SKIP  (no tests/ directory)" % label)
            continue

        passed, count, output = run(directory, verbose)
        total += count
        status = "ok" if passed else "FAIL"
        print("  %-26s %-5s %4d tests" % (label, status, count))
        if not passed:
            failures.append((label, output))
        elif verbose:
            print(output)

    print("=" * 62)
    print("%d tests in %.1fs" % (total, time.monotonic() - started))

    if failures:
        print()
        for label, output in failures:
            print("-" * 62)
            print(label)
            print("-" * 62)
            print(output.strip()[-4000:])
        print()
        print("FAILED: %d of %d suite(s)" % (len(failures), len(suites)))
        return 1

    print("All suites passed.")

    if not wanted:
        # The n8n exports have no unit tests; the linter is their equivalent.
        print()
        print("n8n workflow lint")
        print("=" * 62, flush=True)
        lint = subprocess.run([sys.executable, str(ROOT / "scripts" / "lint_workflows.py")])
        if lint.returncode:
            print("FAILED: workflow lint")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
