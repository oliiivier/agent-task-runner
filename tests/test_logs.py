"""Tests pour `agent-runner logs` et `agent_runner/logs.py`."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_runner.logs import Style, format_log_line, stream_logs  # noqa: E402

BIN = str(ROOT / "bin" / "agent-runner")


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([BIN, *args], cwd=cwd, capture_output=True, text=True)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


class LogFormatTests(unittest.TestCase):
    def test_claude_events_and_raw_mode(self):
        style = Style(color=False)
        events = [
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
            {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "Analysing file"},
                {"type": "text", "text": "Je vais lire le fichier."},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "bin/agent-runner"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "is_error": False, "content": "ligne1\nligne2"},
            ]}},
            {"type": "result", "is_error": False, "num_turns": 2, "total_cost_usd": 0.0125, "duration_ms": 1500,
             "result": "Terminé avec succès."},
        ]
        rendered: list[str] = []
        for ev in events:
            rendered.extend(format_log_line(json.dumps(ev), style))
        joined = "\n".join(rendered)
        self.assertIn("[init] model=claude-sonnet-4-6", joined)
        self.assertIn("[thinking] Analysing file", joined)
        self.assertIn("Je vais lire le fichier.", joined)
        self.assertIn("[tool:Read] bin/agent-runner", joined)
        self.assertIn("[result] ligne1", joined)
        self.assertIn("[terminé] tours=2 coût=$0.0125 durée=1.5s", joined)

        raw_line = json.dumps(events[0])
        self.assertEqual(format_log_line(raw_line, style, raw=True), [raw_line])

    def test_agy_and_codex_events(self):
        style = Style(color=True)
        agy_step = json.dumps({
            "event": "step_update",
            "step_update": {"step_type": "agent_response", "state": "DONE", "text": "Patch appliqué."},
        })
        codex_cmd = json.dumps({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "pytest -q", "exit_code": 0, "aggregated_output": "3 passed"},
        })
        self.assertEqual(format_log_line(agy_step, style), ["Patch appliqué."])
        cmd_out = "\n".join(format_log_line(codex_cmd, style))
        self.assertIn("[cmd]", cmd_out)
        self.assertIn("pytest -q", cmd_out)
        self.assertIn("3 passed", cmd_out)
        self.assertIn("\033[", cmd_out)

    def test_agy_active_step_updates_suppressed_and_done_tools_formatted(self):
        style = Style(color=False)
        active = json.dumps({
            "event": "step_update",
            "step_update": {"conversation_id": "abc", "step_index": 17, "state": "ACTIVE", "step_type": "agent_response"},
        })
        done_tool = json.dumps({
            "event": "step_update",
            "step_update": {
                "step_index": 18,
                "state": "DONE",
                "step_type": "run_command",
                "command_line": "swift test",
                "text": "All 12 tests passed",
            },
        })
        self.assertEqual(format_log_line(active, style), [])
        out = "\n".join(format_log_line(done_tool, style))
        self.assertIn("[run_command] swift test", out)
        self.assertIn("All 12 tests passed", out)

        init_ev = json.dumps({"event": "init", "init": {"model": "gemini-4-hydrogen-medium", "cwd": "/tmp/repo"}})
        self.assertEqual(format_log_line(init_ev, style), ["[init] model=gemini-4-hydrogen-medium cwd=/tmp/repo"])

        tool_extra = json.dumps({
            "event": "step_update",
            "step_update": {"step_index": 2, "state": "DONE", "step_type": "tool", "call": {"name": "read_file", "path": "a.swift"}},
        })
        self.assertIn("[tool:read_file] a.swift", "\n".join(format_log_line(tool_extra, style)))

        agy_tool_info = json.dumps({
            "event": "step_update",
            "step_update": {
                "step_index": 3,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "read_file",
                "duration_seconds": 1.12,
                "tool_info": {
                    "name": "read_file",
                    "parameters": {"Path": "/repo/ios/CLAUDE.md"},
                    "output": "98 lines, 8940 bytes",
                },
            },
        })
        rendered_ti = "\n".join(format_log_line(agy_tool_info, style))
        self.assertIn("[tool:read_file] /repo/ios/CLAUDE.md (1.1s)", rendered_ti)
        self.assertIn("98 lines, 8940 bytes", rendered_ti)

        sys_empty = json.dumps({
            "event": "step_update",
            "step_update": {"step_index": 16, "state": "DONE", "step_type": "system_message", "duration_seconds": 0.0003},
        })
        self.assertEqual(format_log_line(sys_empty, style), [])

        agy_flat_result = json.dumps({
            "conversation_id": "c0abd481",
            "status": "SUCCESS",
            "response": "Portage terminé.",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        })
        flat_out = "\n".join(format_log_line(agy_flat_result, style))
        self.assertIn("[terminé] tokens=100->20", flat_out)
        self.assertIn("Portage terminé.", flat_out)


class LogsCliTests(unittest.TestCase):
    def test_logs_file_batch_and_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)
            base = root / ".agent-tasks"
            for sub in ("todo", "done", "failed", "logs"):
                (base / sub).mkdir(parents=True)
            (base / "done" / "01-alpha.md").write_text("---\nid: 01-alpha\nbatch: lot-a\nbranch: feat/a\n---\nAlpha\n")
            (base / "todo" / "02-beta.md").write_text("---\nid: 02-beta\nbatch: lot-b\nbranch: feat/b\n---\nBeta\n")
            (base / "logs" / "usage.tsv").write_text(
                "timestamp\tbatch\tid\ttry\trunner\tmodel\tturns\ttokens_in\tcache_write\tcache_read\ttokens_out\tcost_usd\tduration_sec\tstatus\n"
                "2026-11-21T10:00:00+00:00\tlot-a\t01-alpha\t1\tclaude\tsonnet\t1\t10\t0\t0\t5\t0.01\t2\tsuccess\n"
            )
            log_a = base / "logs" / "01-alpha.try1-sonnet.log"
            log_a.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Alpha OK"}]}}) + "\n")
            verify_a = base / "logs" / "01-alpha.try1-sonnet.verify.log"
            verify_a.write_text("verify passed\n")

            log_b = base / "logs" / "02-beta.try1-sonnet.log"
            log_b.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Beta OK"}]}}) + "\n")
            # Force mtime ordering so log_b is strictly newer than log_a.
            now = time.time()
            os.utime(log_a, (now - 10, now - 10))
            os.utime(log_b, (now, now))

            # --file + --verify
            res_file = _run_cli(["logs", "--file", str(log_a), "--verify", "--no-pager"], root)
            self.assertEqual(res_file.returncode, 0, res_file.stderr)
            self.assertIn("Alpha OK", res_file.stdout)
            self.assertIn("verify passed", res_file.stdout)

            # --batch lot-a ne doit pas inclure lot-b
            res_batch = _run_cli(["logs", "--batch", "lot-a", "--no-pager"], root)
            self.assertEqual(res_batch.returncode, 0, res_batch.stderr)
            self.assertIn("Alpha OK", res_batch.stdout)
            self.assertNotIn("Beta OK", res_batch.stdout)

            # Sans argument : dernier log modifié (log_b)
            res_latest = _run_cli(["logs", "--no-pager"], root)
            self.assertEqual(res_latest.returncode, 0, res_latest.stderr)
            self.assertIn("Beta OK", res_latest.stdout)
            self.assertNotIn("Alpha OK", res_latest.stdout)

    def test_stream_logs_follows_append_and_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            f1 = logs_dir / "01-task.try1-sonnet.log"
            f1.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "étape 1"}]}}) + "\n")
            buf = io.StringIO()
            stop = threading.Event()
            t = threading.Thread(
                target=stream_logs,
                args=([f1], logs_dir),
                kwargs={
                    "batch_ids": {"01-task", "02-task"},
                    "style": Style(color=False),
                    "poll_interval": 0.02,
                    "out": buf,
                    "stop_event": stop,
                },
                daemon=True,
            )
            t.start()
            time.sleep(0.08)
            with f1.open("a") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "étape 2"}]}}) + "\n")
                fh.flush()
            time.sleep(0.08)
            f2 = logs_dir / "02-task.try1-sonnet.log"
            f2.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "tâche suivante"}]}}) + "\n")
            time.sleep(0.1)
            stop.set()
            t.join(timeout=2)

            out = buf.getvalue()
            self.assertIn("étape 1", out)
            self.assertIn("étape 2", out)
            self.assertIn("02-task.try1-sonnet.log", out)
            self.assertIn("tâche suivante", out)

    def test_stream_logs_ignores_older_files_and_detects_overwritten_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            now = time.time()
            f_old = logs_dir / "00-old.try1-sonnet.log"
            f_old.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ancien log"}]}}) + "\n")
            f_cur = logs_dir / "01-cur.try1-sonnet.log"
            f_cur.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "log courant"}]}}) + "\n")
            f_reused = logs_dir / "02-reused.try1-sonnet.log"
            f_reused.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "vieux run"}]}}) + "\n")
            os.utime(f_old, (now - 30, now - 30))
            os.utime(f_reused, (now - 20, now - 20))
            os.utime(f_cur, (now - 10, now - 10))

            buf = io.StringIO()
            stop = threading.Event()
            t = threading.Thread(
                target=stream_logs,
                args=([f_cur], logs_dir),
                kwargs={"style": Style(color=False), "poll_interval": 0.02, "out": buf, "stop_event": stop},
                daemon=True,
            )
            t.start()
            time.sleep(0.08)
            # Simule un --resume qui réécrit 02-reused.try1-sonnet.log avec un mtime plus récent
            f_reused.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "nouveau run"}]}}) + "\n")
            os.utime(f_reused, (now + 5, now + 5))
            time.sleep(0.1)
            stop.set()
            t.join(timeout=2)

            out = buf.getvalue()
            self.assertNotIn("ancien log", out)
            self.assertIn("log courant", out)
            self.assertIn("nouveau run", out)


if __name__ == "__main__":
    unittest.main()
