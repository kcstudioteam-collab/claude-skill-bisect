#!/usr/bin/env python3
"""ddmin: shrink a failing input to a 1-minimal core.

Implements Zeller & Hildebrandt's delta debugging algorithm (ddmin). Given an
input that triggers a bug and a predicate that reports whether the bug is still
present, repeatedly removes chunks until nothing further can be removed.

Predicate convention (identical to `git bisect run`):

    exit 0        -> bug ABSENT   ("good")
    exit non-zero -> bug PRESENT  ("bad")

A raw failing test command already follows this convention, so it can be passed
through unchanged.

Guarantee: the result is *1-minimal* -- deleting any single remaining unit makes
the bug disappear. This is not the same as the globally smallest input; see
references/recipes.md.

Usage:
    ddmin.py --predicate 'pytest -q -x repro_test.py' --placeholder '{}' input.json
    ddmin.py --predicate 'make build 2>&1 | grep -q ERROR' --in-place config.yaml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

__all__ = ["ddmin", "split_into"]


def split_into(units: Sequence[str], n: int) -> list[list[str]]:
    """Split `units` into `n` chunks of near-equal size, dropping empty ones."""
    if n <= 0:
        raise ValueError("n must be positive")
    total = len(units)
    chunks: list[list[str]] = []
    start = 0
    for i in range(n):
        end = (i + 1) * total // n
        if end > start:
            chunks.append(list(units[start:end]))
        start = end
    return chunks


def ddmin(
    units: Sequence[str],
    reproduces: Callable[[Sequence[str]], bool],
) -> list[str]:
    """Return a 1-minimal subsequence of `units` that still reproduces.

    `reproduces(subset)` must return True when the bug is still present.
    The caller is responsible for having verified that the full input
    reproduces and that the empty input does not.
    """
    current = list(units)
    n = 2

    while len(current) >= 2:
        chunks = split_into(current, n)

        # Phase 1: can one chunk alone reproduce? (fast, aggressive shrink)
        for chunk in chunks:
            if reproduces(chunk):
                current, n = chunk, 2
                break
        else:
            # Phase 2: can we drop one chunk and still reproduce?
            for i, chunk in enumerate(chunks):
                complement = [u for j, c in enumerate(chunks) if j != i for u in c]
                if complement and reproduces(complement):
                    current, n = complement, max(n - 1, 2)
                    break
            else:
                # Phase 3: no progress at this granularity -- go finer.
                if n >= len(current):
                    break
                n = min(n * 2, len(current))

    return current


class PredicateRunner:
    """Runs a shell predicate against candidate inputs, with memoisation."""

    def __init__(
        self,
        command: str,
        target: Path,
        *,
        placeholder: str | None,
        in_place: bool,
        joiner: str,
        verbose: bool,
    ) -> None:
        self.command = command
        self.target = target
        self.placeholder = placeholder
        self.in_place = in_place
        self.joiner = joiner
        self.verbose = verbose
        self.calls = 0
        self._cache: dict[tuple[str, ...], bool] = {}

    def __call__(self, units: Sequence[str]) -> bool:
        key = tuple(units)
        if key in self._cache:
            return self._cache[key]

        self.calls += 1
        content = self.joiner.join(units)
        if content and not content.endswith("\n"):
            content += "\n"

        if self.in_place:
            self.target.write_text(content)
            command = self.command
            result = self._run(command)
        else:
            suffix = self.target.suffix
            with tempfile.NamedTemporaryFile(
                "w", suffix=suffix, delete=False, encoding="utf-8"
            ) as handle:
                handle.write(content)
                candidate = Path(handle.name)
            try:
                assert self.placeholder is not None
                command = self.command.replace(self.placeholder, str(candidate))
                result = self._run(command)
            finally:
                candidate.unlink(missing_ok=True)

        if self.verbose:
            verdict = "BAD (reproduces)" if result else "good"
            print(
                f"  [{self.calls:>3}] {len(units):>5} units -> {verdict}",
                file=sys.stderr,
            )
        self._cache[key] = result
        return result

    def _run(self, command: str) -> bool:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
        )
        # Non-zero exit == bug present, matching `git bisect run`.
        return completed.returncode != 0


def _fail(message: str) -> None:
    print(f"ddmin: {message}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ddmin.py",
        description="Shrink a failing input to a 1-minimal core.",
    )
    parser.add_argument("input", type=Path, help="file that triggers the bug")
    parser.add_argument(
        "--predicate",
        required=True,
        help="shell command; exit 0 = bug absent, non-zero = bug present",
    )
    parser.add_argument(
        "--placeholder",
        default="{}",
        help="token in the predicate replaced by the candidate path (default: {})",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="overwrite the input file instead of using a temp copy "
        "(required when the predicate cannot take a path)",
    )
    parser.add_argument(
        "--unit",
        choices=("line", "char"),
        default="line",
        help="granularity to shrink at (default: line)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write the minimised input here (default: stdout)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    args = parser.parse_args(argv)

    if not args.input.is_file():
        _fail(f"{args.input} is not a file")

    original = args.input.read_text()
    if args.unit == "line":
        units: list[str] = original.splitlines()
        joiner = "\n"
    else:
        units = list(original)
        joiner = ""

    if not units:
        _fail("input is empty, nothing to minimise")

    uses_placeholder = args.placeholder in args.predicate
    if not uses_placeholder and not args.in_place:
        _fail(
            f"predicate contains no {args.placeholder!r} placeholder.\n"
            "  Either add one so the candidate path can be substituted, or pass\n"
            "  --in-place to let ddmin overwrite the input file (a backup is kept)."
        )

    backup: Path | None = None
    if args.in_place:
        backup = args.input.with_suffix(args.input.suffix + ".ddmin-backup")
        backup.write_text(original)
        if not args.quiet:
            print(f"ddmin: backup written to {backup}", file=sys.stderr)

    runner = PredicateRunner(
        args.predicate,
        args.input,
        placeholder=args.placeholder if uses_placeholder else None,
        in_place=args.in_place,
        joiner=joiner,
        verbose=not args.quiet,
    )

    try:
        # Sanity checks. Skipping these is the most common way to get a
        # confident, wrong answer.
        if not runner(units):
            _fail(
                "the full input does NOT reproduce (predicate exited 0).\n"
                "  Check the predicate polarity: non-zero exit must mean 'bug present'."
            )
        if runner([]):
            _fail(
                "the EMPTY input still reproduces (predicate exited non-zero).\n"
                "  The bug is not caused by this file's contents, so there is\n"
                "  nothing here to minimise. Bisect a different axis."
            )

        if not args.quiet:
            print(
                f"ddmin: minimising {len(units)} {args.unit}s...",
                file=sys.stderr,
            )
        minimal = ddmin(units, runner)
    finally:
        if backup is not None:
            args.input.write_text(original)

    result = joiner.join(minimal)
    if result and not result.endswith("\n"):
        result += "\n"

    if args.output:
        args.output.write_text(result)
        destination = str(args.output)
    else:
        sys.stdout.write(result)
        destination = "stdout"

    if not args.quiet:
        removed = len(units) - len(minimal)
        pct = 100.0 * removed / len(units)
        print(
            f"ddmin: {len(units)} -> {len(minimal)} {args.unit}s "
            f"({pct:.1f}% removed) in {runner.calls} predicate runs -> {destination}",
            file=sys.stderr,
        )
        if backup is not None:
            print(f"ddmin: original restored; backup at {backup}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
