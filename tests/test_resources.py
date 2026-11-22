import os
import shutil
import subprocess
import tempfile
import unittest
import zipapp
from pathlib import Path
from unittest import mock

from agent_runner import task as task_mod


class ResourceTests(unittest.TestCase):
    def test_read_tool_resource_from_repo(self) -> None:
        tpl = task_mod.read_tool_resource("templates", "example-task.md")
        self.assertIn("id:", tpl)
        skill = task_mod.read_tool_resource("skills", "plan-tasks", "SKILL.md")
        self.assertTrue(len(skill) > 0)

    def test_read_tool_resource_missing(self) -> None:
        self.assertEqual(task_mod.read_tool_resource("nope", "missing.txt", default="fallback"), "fallback")
        with self.assertRaises(FileNotFoundError):
            task_mod.read_tool_resource("nope", "missing.txt")

    def test_read_tool_resource_from_zipapp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stage = tmp_path / "stage"
            shutil.copytree(task_mod.TOOL_ROOT / "agent_runner", stage / "agent_runner",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(task_mod.TOOL_ROOT / "templates", stage / "templates")
            shutil.copytree(task_mod.TOOL_ROOT / "skills", stage / "skills")
            archive = tmp_path / "agent-runner"
            zipapp.create_archive(stage, target=archive, interpreter="/usr/bin/env python3",
                                  main="agent_runner.cli:main")

            with mock.patch.object(task_mod, "TOOL_ROOT", archive):
                tpl = task_mod.read_tool_resource("templates", "example-task.md")
                self.assertIn("id:", tpl)
                skill = task_mod.read_tool_resource("skills", "plan-tasks", "SKILL.md")
                self.assertTrue(len(skill) > 0)
                self.assertEqual(task_mod.read_tool_resource("nope", "missing.txt", default=""), "")
                with self.assertRaises(FileNotFoundError):
                    task_mod.read_tool_resource("nope", "missing.txt")

            res = subprocess.run(
                [str(archive), "--help"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("agent-runner", res.stdout)

    def test_install_sh_builds_zipapp_and_uninstalls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            env = {
                **os.environ,
                "BIN_DIR": str(bin_dir),
                "CLAUDE_SKILLS_DIR": str(tmp_path / "claude-skills"),
                "AGY_SKILLS_DIR": str(tmp_path / "agy-skills"),
                "CODEX_HOME": str(tmp_path / "codex-missing"),
            }
            install_sh = str(task_mod.TOOL_ROOT / "install.sh")
            res = subprocess.run(["bash", install_sh], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)

            binary = bin_dir / "agent-runner"
            self.assertTrue(binary.is_file() and not binary.is_symlink())
            help_res = subprocess.run([str(binary), "--help"], capture_output=True, text=True, check=False)
            self.assertEqual(help_res.returncode, 0, help_res.stderr)

            unres = subprocess.run(["bash", install_sh, "--uninstall"], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(unres.returncode, 0, unres.stderr)
            self.assertFalse(binary.exists())
