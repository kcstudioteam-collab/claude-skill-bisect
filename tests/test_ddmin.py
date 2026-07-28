"""Tests for ddmin. Standard library only -- run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DDMIN_PATH = ROOT / "scripts" / "ddmin.py"

_spec = importlib.util.spec_from_file_location("ddmin", DDMIN_PATH)
assert _spec and _spec.loader
ddmin_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ddmin_mod)

ddmin = ddmin_mod.ddmin
split_into = ddmin_mod.split_into


class TestSplitInto(unittest.TestCase):
    def test_splits_evenly(self) -> None:
        self.assertEqual(split_into(["a", "b", "c", "d"], 2), [["a", "b"], ["c", "d"]])

    def test_handles_uneven_split(self) -> None:
        chunks = split_into(["a", "b", "c"], 2)
        self.assertEqual([u for c in chunks for u in c], ["a", "b", "c"])
        self.assertEqual(len(chunks), 2)

    def test_drops_empty_chunks_when_n_exceeds_length(self) -> None:
        chunks = split_into(["a", "b"], 5)
        self.assertEqual(chunks, [["a"], ["b"]])

    def test_rejects_non_positive_n(self) -> None:
        with self.assertRaises(ValueError):
            split_into(["a"], 0)


class TestDdmin(unittest.TestCase):
    def test_isolates_single_poison_unit(self) -> None:
        units = [f"line{i}" for i in range(100)]
        units[42] = "POISON"

        def reproduces(subset: Sequence[str]) -> bool:
            return "POISON" in subset

        self.assertEqual(ddmin(units, reproduces), ["POISON"])

    def test_isolates_a_conjunction(self) -> None:
        """A bug needing two units together must keep both."""
        units = [f"line{i}" for i in range(64)]
        units[5] = "OPEN"
        units[60] = "CLOSE"

        def reproduces(subset: Sequence[str]) -> bool:
            return "OPEN" in subset and "CLOSE" in subset

        result = ddmin(units, reproduces)
        self.assertEqual(sorted(result), ["CLOSE", "OPEN"])

    def test_result_is_one_minimal(self) -> None:
        """Removing any single remaining unit must stop reproduction."""
        units = [f"line{i}" for i in range(40)]
        units[3], units[17], units[31] = "A", "B", "C"

        def reproduces(subset: Sequence[str]) -> bool:
            return all(marker in subset for marker in ("A", "B", "C"))

        result = ddmin(units, reproduces)
        self.assertTrue(reproduces(result))
        for i in range(len(result)):
            shrunk = result[:i] + result[i + 1 :]
            self.assertFalse(
                reproduces(shrunk),
                f"removing {result[i]!r} still reproduces -- not 1-minimal",
            )

    def test_preserves_order(self) -> None:
        units = ["z", "KEEP1", "y", "KEEP2", "x"]

        def reproduces(subset: Sequence[str]) -> bool:
            return "KEEP1" in subset and "KEEP2" in subset

        self.assertEqual(ddmin(units, reproduces), ["KEEP1", "KEEP2"])

    def test_returns_input_when_nothing_removable(self) -> None:
        units = ["a", "b"]

        def reproduces(subset: Sequence[str]) -> bool:
            return len(subset) == 2

        self.assertEqual(ddmin(units, reproduces), ["a", "b"])

    def test_single_unit_input_is_returned_untouched(self) -> None:
        self.assertEqual(ddmin(["only"], lambda s: True), ["only"])

    def test_uses_logarithmic_number_of_probes(self) -> None:
        """A single poison line in 1024 should cost far fewer than 1024 probes."""
        units = [f"line{i}" for i in range(1024)]
        units[900] = "POISON"
        calls = 0

        def reproduces(subset: Sequence[str]) -> bool:
            nonlocal calls
            calls += 1
            return "POISON" in subset

        self.assertEqual(ddmin(units, reproduces), ["POISON"])
        self.assertLess(calls, 100, f"took {calls} probes, expected far fewer")


class TestCommandLine(unittest.TestCase):
    """End-to-end runs of the actual script, via a real shell predicate."""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DDMIN_PATH), *args],
            capture_output=True,
            text=True,
        )

    def test_minimises_a_real_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.txt"
            lines = [f"harmless{i}" for i in range(50)]
            lines[20] = "BOOM"
            target.write_text("\n".join(lines) + "\n")

            # grep exits 0 on match, so invert: non-zero == BOOM present.
            result = self._run(
                ["--predicate", "! grep -q BOOM {}", "--quiet", str(target)]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "BOOM")

    def test_rejects_predicate_that_does_not_reproduce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.txt"
            target.write_text("nothing interesting\n")
            result = self._run(
                ["--predicate", "! grep -q BOOM {}", "--quiet", str(target)]
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("does NOT reproduce", result.stderr)

    def test_rejects_predicate_that_always_reproduces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.txt"
            target.write_text("a\nb\nc\n")
            # `false {}` ignores the path and always exits 1 == always "bad".
            result = self._run(["--predicate", "false {}", "--quiet", str(target)])
            self.assertEqual(result.returncode, 2)
            self.assertIn("EMPTY input still reproduces", result.stderr)

    def test_requires_placeholder_or_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.txt"
            target.write_text("a\nb\n")
            result = self._run(["--predicate", "true", "--quiet", str(target)])
            self.assertEqual(result.returncode, 2)
            self.assertIn("no '{}' placeholder", result.stderr)

    def test_in_place_restores_the_original_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.txt"
            lines = [f"setting{i}=0" for i in range(20)]
            lines[7] = "BROKEN=1"
            original = "\n".join(lines) + "\n"
            target.write_text(original)

            result = self._run(
                [
                    "--predicate",
                    f"! grep -q BROKEN {target}",
                    "--in-place",
                    "--quiet",
                    "-o",
                    str(Path(tmp) / "min.txt"),
                    str(target),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), original, "original was not restored")
            self.assertEqual(
                (Path(tmp) / "min.txt").read_text().strip(), "BROKEN=1"
            )

    def test_char_granularity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.txt"
            target.write_text("aaaaaXaaaaa")
            result = self._run(
                [
                    "--predicate",
                    "! grep -q X {}",
                    "--unit",
                    "char",
                    "--quiet",
                    str(target),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "X")


if __name__ == "__main__":
    unittest.main()
