import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE_OUTLINE = ROOT / "helpers" / "file-outline"


class FileOutlineTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["python3", str(FILE_OUTLINE), *args], capture_output=True, text=True)

    def test_python_with_decorators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "app.py"
            p.write_text(
                "@dataclass\n"
                "class RemoteSyncManager(Base):\n"
                "    @property\n"
                "    def active(self) -> bool:\n"
                "        return True\n\n"
                "async def sync() -> None:\n"
                "    pass\n"
            )
            res = self._run(str(p))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(
                res.stdout.strip().splitlines(),
                [
                    "1-5  class RemoteSyncManager(Base)",
                    "3-5    def active(...) -> bool",
                    "7-8  async def sync(...) -> None",
                ],
            )

    def test_rust_and_kotlin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rs = Path(tmp) / "lib.rs"
            rs.write_text(
                "pub struct Engine {\n    id: u32,\n}\n\n"
                "impl Engine {\n    pub fn run(&self) -> bool {\n        // }\n        true\n    }\n}\n"
            )
            kt = Path(tmp) / "Manager.kt"
            kt.write_text("class Manager {\n    suspend fun load(id: String): Int {\n        return 1\n    }\n}\n")

            r_rs = self._run(str(rs))
            self.assertEqual(r_rs.returncode, 0, r_rs.stderr)
            self.assertIn("1-3  struct Engine", r_rs.stdout)
            self.assertIn("5-10  impl Engine", r_rs.stdout)
            self.assertIn("6-9    fn run(&self) -> bool", r_rs.stdout)

            r_kt = self._run(str(kt))
            self.assertEqual(r_kt.returncode, 0, r_kt.stderr)
            self.assertIn("1-5  class Manager", r_kt.stdout)
            self.assertIn("2-4    fun load(id: String): Int", r_kt.stdout)

    def test_terraform_with_heredoc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "main.tf"
            tf.write_text(
                'resource "aws_iam_policy" "p" {\n'
                '  policy = <<-EOF\n'
                '    {\n'
                '      "Version": "2012-10-17"\n'
                '    }\n'
                '  EOF\n'
                '}\n\n'
                'output "arn" {\n'
                '  value = aws_iam_policy.p.arn\n'
                '}\n'
            )
            res = self._run(str(tf))
            self.assertEqual(res.returncode, 0, r_rs := res.stderr)
            self.assertEqual(
                res.stdout.strip().splitlines(),
                [
                    '1-7  resource "aws_iam_policy" "p"',
                    '9-11  output "arn"',
                ],
            )

    def test_ansible_playbook_and_yaml_compose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pb = Path(tmp) / "site.yml"
            pb.write_text(
                "---\n"
                "- name: Configure web\n"
                "  hosts: web\n"
                "  tasks:\n"
                "    - name: Install Docker\n"
                "      apt:\n"
                "        name: docker.io\n"
                "    - name: Start Docker\n"
                "      service:\n"
                "        name: docker\n"
            )
            res_pb = self._run(str(pb))
            self.assertEqual(res_pb.returncode, 0, res_pb.stderr)
            self.assertEqual(
                res_pb.stdout.strip().splitlines(),
                [
                    "2-10  - name: Configure web",
                    "4-10    tasks:",
                    "5-7    - name: Install Docker",
                    "8-10    - name: Start Docker",
                ],
            )

            dc = Path(tmp) / "compose.yaml"
            dc.write_text(
                "services:\n"
                "  api:\n"
                "    image: python:3.11\n"
                "  db:\n"
                "    image: postgres:16\n"
                "volumes:\n"
                "  data: {}\n"
            )
            res_dc = self._run(str(dc))
            self.assertEqual(res_dc.returncode, 0, res_dc.stderr)
            self.assertEqual(
                res_dc.stdout.strip().splitlines(),
                [
                    "1-5  services:",
                    "2-3    api:",
                    "4-5    db:",
                    "6-7  volumes:",
                ],
            )

    def test_markdown_ignores_fenced_code_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "plan.md"
            md.write_text(
                "# Titre\n\n"
                "```python\n"
                "# commentaire ignore\n"
                "x = 1\n"
                "```\n\n"
                "## Section\n"
                "texte\n"
            )
            res = self._run(str(md))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(
                res.stdout.strip().splitlines(),
                [
                    "1-9  # Titre",
                    "8-9    ## Section",
                ],
            )

    def test_toml_and_unknown_format_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "config.toml"
            toml.write_text("[routing]\na = 1\n\n[[items]]\nb = 2\n")
            res_t = self._run(str(toml))
            self.assertEqual(res_t.returncode, 0, res_t.stderr)
            self.assertEqual(res_t.stdout.strip().splitlines(), ["1-3  [routing]", "4-5  [[items]]"])

            unk = Path(tmp) / "notes.xyz"
            unk.write_text("ligne 1\nligne 2\nligne 3\n")
            res_u = self._run(str(unk))
            self.assertEqual(res_u.returncode, 0, res_u.stderr)
            self.assertIn("3 lignes", res_u.stdout)

            res_missing = self._run("/nonexistent/missing.py")
            self.assertEqual(res_missing.returncode, 1)


if __name__ == "__main__":
    unittest.main()
