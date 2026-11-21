import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLACE_BLOCK = ROOT / "helpers" / "replace-block"


class ReplaceBlockTests(unittest.TestCase):
    def _run(self, *args: str, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(REPLACE_BLOCK), *args],
            input=stdin_text,
            capture_output=True,
            text=True,
        )

    def test_replace_from_stdin_with_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "code.py"
            p.write_text("line1\nold_a\nold_b\nline4\n")
            res = self._run(
                "--expected-lines", "4",
                "--anchor", "old_a",
                str(p), "2", "3",
                stdin_text="new_a\nnew_b\nnew_c\n",
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(p.read_text(), "line1\nnew_a\nnew_b\nnew_c\nline4\n")

    def test_replace_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            rep = Path(tmp) / "rep.txt"
            target.write_text("a\nb\nc\nd\n")
            rep.write_text("X\n")
            res = self._run(str(target), "2", "3", str(rep))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(target.read_text(), "a\nX\nd\n")

    def test_guard_failures_leave_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "code.py"
            original = "a\nb\nc\n"
            p.write_text(original)

            r_len = self._run("--expected-lines", "99", str(p), "1", "2", stdin_text="x\n")
            self.assertNotEqual(r_len.returncode, 0)
            self.assertEqual(p.read_text(), original)

            r_anc = self._run("--anchor", "missing", str(p), "1", "2", stdin_text="x\n")
            self.assertNotEqual(r_anc.returncode, 0)
            self.assertEqual(p.read_text(), original)

            r_rng = self._run(str(p), "2", "10", stdin_text="x\n")
            self.assertNotEqual(r_rng.returncode, 0)
            self.assertEqual(p.read_text(), original)


if __name__ == "__main__":
    unittest.main()
