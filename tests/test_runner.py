"""Tests d'intégration : dépôts Git temporaires + faux CLI `claude` placé dans le PATH."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "bin" / "agent-runner"
MOVE = ROOT / "helpers" / "move-lines"

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
argv = sys.argv[1:]
if argv == ["auth", "status"]:
    logged = os.environ.get("FAKE_CLAUDE_LOGGED_OUT") != "1"
    print(json.dumps({"loggedIn": logged}))
    sys.exit(0 if logged else 1)
prompt = argv[argv.index("-p") + 1]
model = argv[argv.index("--model") + 1]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"model": model, "prompt": prompt, "argv": argv}) + "\n")
def csv(name): return [m for m in os.environ.get(name, "").split(",") if m]
n = len(open(os.environ["FAKE_LOG"]).read().splitlines())
if model in csv("FAKE_QUOTA_MODELS"):
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "num_turns": 0,
                      "result": "Claude AI usage limit reached|1764000000"}))
    sys.exit(1)
turns = int(os.environ.get("FAKE_TURNS", "1"))
for i in range(turns):
    print(json.dumps({"type": "assistant", "message": {"id": f"m{n}-{i}"}}), flush=True)
if model in csv("FAKE_FAIL_MODELS"):
    pass  # ne produit rien : pas de commit
else:
    open(f"work-{n}.txt", "w").write(model)
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"feat: work {n} by {model}"], check=True)
if model in csv("FAKE_DIRTY_MODELS"):
    open("README.md", "a").write("dirty\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
                  "result": "ok", "total_cost_usd": 0.01,
                  "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5,
                            "cache_read_input_tokens": 3, "output_tokens": 7}}))
'''

FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
argv = sys.argv[1:]
if os.environ.get("FAKE_AGY_LOGGED_OUT") == "1":
    print("Error: not signed in", file=sys.stderr)
    sys.exit(1)
if argv == ["models"]:
    print("Fetching available models...")
    for m in ["gemini-4-neon-high", "gemini-4-neon-xhigh", "gemini-4-neon-medium", "gemini-4-neon-low",
              "gemini-4-argon-low", "gemini-4-argon-medium", "gemini-4-argon-high",
              "gemini-4-hydrogen-low", "gemini-4-hydrogen-medium", "gemini-4-hydrogen-high",
              "gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high",
              "claude-opus-5-5-high", "gemini-pro-orchestrator-neon"]:
        print(f"{m}\tLabel")
    sys.exit(0)
prompt = argv[argv.index("--print") + 1]
model = argv[argv.index("--model") + 1]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"runner": "agy", "model": model, "prompt": prompt, "argv": argv}) + "\n")
n = len(open(os.environ["FAKE_LOG"]).read().splitlines())
open(f"agy-{n}.txt", "w").write(model)
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"feat: agy work {n}"], check=True)
print(json.dumps({"result": "ok", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''

FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
argv = sys.argv[1:]
logged = os.environ.get("FAKE_CODEX_LOGGED_IN") == "1"
if argv == ["login", "status"]:
    print("Logged in using ChatGPT" if logged else "Not logged in")
    sys.exit(0 if logged else 1)
assert argv[0] == "exec" and argv[-2] == "--", argv
model = argv[argv.index("--model") + 1]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"runner": "codex", "model": model, "prompt": argv[-1], "argv": argv}) + "\n")
n = len(open(os.environ["FAKE_LOG"]).read().splitlines())
print(json.dumps({"type": "thread.started", "thread_id": "t"}))
print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "git commit"}}))
open(f"codex-{n}.txt", "w").write(model)
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"feat: codex work {n}"], check=True)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 4, "cached_input_tokens": 2,
                                                      "output_tokens": 3}}))
'''


def task_md(id_, **kw) -> str:
    meta = {"id": id_, "branch": kw.pop("branch", "feat/x"), **kw}
    lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in meta.items()] + ["---", "", f"## Objectif\nFaire {id_}.", ""]
    return "\n".join(lines)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        bindir = tmp / "bin"
        bindir.mkdir()
        fake = bindir / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        agy = bindir / "agy"
        agy.write_text(FAKE_AGY)
        agy.chmod(0o755)
        codex = bindir / "codex"  # déconnecté par défaut : codex indisponible, comme s'il était absent
        codex.write_text(FAKE_CODEX)
        codex.chmod(0o755)
        self.fake_log = tmp / "fake.log"
        self.fake_log.write_text("")
        self.env = {
            **{k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")},
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "FAKE_LOG": str(self.fake_log),
            "XDG_CONFIG_HOME": str(tmp / "xdg"),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }
        self.git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("hello\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.repo, env=self.env, capture_output=True, text=True, check=True).stdout

    def runner(self, *a, **env):
        return subprocess.run([sys.executable, str(RUNNER), *a], cwd=self.repo, env={**self.env, **env},
                              capture_output=True, text=True)

    def init(self, *a):
        r = self.runner("init", *a)
        self.assertEqual(r.returncode, 0, r.stderr)

    def add_task(self, name, **kw):
        (self.repo / ".agent-tasks" / "todo" / name).write_text(task_md(name.removesuffix(".md"), **kw))

    def calls(self):
        return [json.loads(ln) for ln in self.fake_log.read_text().splitlines()]

    def usage_rows(self):
        lines = (self.repo / ".agent-tasks/logs/usage.tsv").read_text().splitlines()
        return [ln.split("\t") for ln in lines[1:]]

    def listing(self, d):
        return sorted(p.name for p in (self.repo / ".agent-tasks" / d).glob("*.md"))


class InitTests(Base):
    def test_init_creates_tree_and_is_idempotent(self):
        (self.repo / "Cargo.toml").write_text("[package]\n")
        self.init()
        base = self.repo / ".agent-tasks"
        for d in ("todo", "done", "failed", "logs"):
            self.assertTrue((base / d).is_dir())
        self.assertTrue((base / "todo/00-example.md.disabled").exists())
        cfg = (base / "config.toml").read_text()
        self.assertIn('default_verify_cmd = "cargo test --quiet"', cfg)
        self.assertIn("Bash(cargo:*)", cfg)
        self.assertIn("Bash(move-lines:*)", cfg)
        (base / "config.toml").write_text(cfg + "# custom\n")
        self.init()
        self.assertIn("# custom", (base / "config.toml").read_text())
        exclude = (self.repo / ".git/info/exclude").read_text()
        self.assertEqual(exclude.count(".agent-tasks/"), 1)
        self.git("check-ignore", "-q", ".agent-tasks")

    def test_gitignore_and_agent_rules(self):
        self.init("--gitignore", "--with-agent-rules")
        self.init("--gitignore", "--with-agent-rules")
        self.assertEqual((self.repo / ".gitignore").read_text().count(".agent-tasks/"), 1)
        self.assertEqual((self.repo / "CLAUDE.md").read_text().count("BEGIN agent-task-runner"), 1)

    def test_default_runner_follows_available_runners_and_is_idempotent(self):
        cfg_path = self.repo / ".agent-tasks/config.toml"
        OUT = {"FAKE_CLAUDE_LOGGED_OUT": "1"}
        r = self.runner("init", **OUT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("non connecté", r.stdout)
        self.assertEqual(tomllib.loads(cfg_path.read_text())["default_runner"], "agy")
        cfg_path.write_text(cfg_path.read_text() + "# custom\n")
        before = cfg_path.read_text()
        for env in (OUT, {}):  # relances : agy reste utilisable -> fichier strictement inchangé
            r = self.runner("init", **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(cfg_path.read_text(), before)
        r = self.runner("init", FAKE_AGY_LOGGED_OUT="1")  # agy déconnecté -> bascule sur claude
        self.assertIn("default_runner : agy -> claude", r.stdout)
        after = cfg_path.read_text()
        self.assertEqual(after, before.replace('default_runner = "agy"', 'default_runner = "claude"'))
        r = self.runner("init", FAKE_AGY_LOGGED_OUT="1", FAKE_CLAUDE_LOGGED_OUT="1")  # aucun : inchangé
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("aucun runner utilisable", r.stderr)
        self.assertEqual(cfg_path.read_text(), after)

    def test_default_runner_inserted_before_first_table(self):
        base = self.repo / ".agent-tasks"
        base.mkdir()
        (base / "config.toml").write_text('# perso\nmain_branch = "main"\n\n[extra_args]\nclaude = []\n')
        r = self.runner("init", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (base / "config.toml").read_text()
        cfg = tomllib.loads(text)
        self.assertEqual(cfg["default_runner"], "agy")
        self.assertNotIn("default_runner", cfg["extra_args"])
        self.assertTrue(text.startswith("# perso\n"))
        self.runner("init", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertEqual((base / "config.toml").read_text(), text)

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(RUNNER), "init"], cwd=d, env=self.env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)


class RunTests(Base):
    def setUp(self):
        super().setUp()
        self.init()

    def test_success_with_cheapest_model(self):
        self.add_task("01-a.md")
        r = self.runner("run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.listing("done"), ["01-a.md"])
        self.assertEqual([c["model"] for c in self.calls()], ["haiku"])
        self.assertEqual(self.git("branch", "--show-current").strip(), "feat/x")
        rows = self.usage_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2:6], ["01-a", "1", "claude", "haiku"])
        self.assertEqual(rows[0][-1], "success")
        logs = list((self.repo / ".agent-tasks/logs").glob("01-a.try1-haiku.*"))
        self.assertEqual({p.suffix for p in logs}, {".log", ".json"})

    def test_escalation_after_failure_keeps_partial_work_and_injects_report(self):
        self.add_task("01-a.md", max_model="sonnet")
        r = self.runner("run", FAKE_FAIL_MODELS="haiku")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        calls = self.calls()
        self.assertEqual([c["model"] for c in calls], ["haiku", "sonnet"])
        self.assertNotIn("ESCALADE", calls[0]["prompt"])
        self.assertIn("ESCALADE", calls[1]["prompt"])
        self.assertIn("HEAD n'a pas avancé", calls[1]["prompt"])
        self.assertEqual([r_[-1] for r_ in self.usage_rows()], ["no_commit", "success"])
        self.assertEqual(self.listing("done"), ["01-a.md"])

    def test_max_model_caps_escalation_strict_mode_keeps_task_in_todo(self):
        self.add_task("01-a.md", max_model="sonnet")
        self.add_task("02-b.md")
        r = self.runner("run", FAKE_FAIL_MODELS="haiku,sonnet,opus")
        self.assertEqual(r.returncode, 1)
        self.assertEqual([c["model"] for c in self.calls()], ["haiku", "sonnet"])
        self.assertEqual(self.listing("todo"), ["01-a.md", "02-b.md"])

    def test_no_escalate_and_forced_model(self):
        self.add_task("01-a.md")
        r = self.runner("run", "--no-escalate", "--model", "opus", FAKE_FAIL_MODELS="opus")
        self.assertEqual(r.returncode, 1)
        self.assertEqual([c["model"] for c in self.calls()], ["opus"])

    def test_continue_on_error_rolls_back_and_continues(self):
        (self.repo / ".agent-tasks/todo/01-bad.md").write_text(task_md("01-bad", escalate=False, branch="feat/bad"))
        self.add_task("02-ok.md", branch="feat/ok", model="sonnet")
        r = self.runner("run", "--continue-on-error", FAKE_FAIL_MODELS="haiku", FAKE_DIRTY_MODELS="haiku")
        self.assertEqual(r.returncode, 1)  # au moins un échec
        self.assertEqual(self.listing("failed"), ["01-bad.md"])
        self.assertEqual(self.listing("done"), ["02-ok.md"])
        report = (self.repo / ".agent-tasks/failed/01-bad.md").read_text()
        self.assertIn("Rapport d'échec", report)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no"), "")
        self.assertNotIn("dirty", (self.repo / "README.md").read_text())

    def test_verify_cmd_failure_triggers_escalation(self):
        verify = "test -f work-2.txt"  # n'existe qu'après la 2e invocation de l'agent
        self.add_task("01-a.md", verify_cmd=verify, max_model="sonnet")
        r = self.runner("run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([c["model"] for c in self.calls()], ["haiku", "sonnet"])
        self.assertIn("verify_cmd en échec", self.calls()[1]["prompt"])
        self.assertEqual([x[-1] for x in self.usage_rows()], ["verify_failed", "success"])

    def test_dirty_tree_after_agent_fails_c2(self):
        self.add_task("01-a.md", escalate=False)
        r = self.runner("run", FAKE_DIRTY_MODELS="haiku")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.usage_rows()[0][-1], "dirty")

    def test_max_turns_exceeded(self):
        self.add_task("01-a.md", escalate=False, max_turns=2)
        r = self.runner("run", FAKE_TURNS="5")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.usage_rows()[0][-1], "max_turns")

    def test_require_commit_false(self):
        self.add_task("01-a.md", require_commit=False)
        r = self.runner("run", FAKE_FAIL_MODELS="haiku")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.listing("done"), ["01-a.md"])

    def test_dirty_tree_refused_without_resume_accepted_with(self):
        self.add_task("01-a.md", branch="main")
        (self.repo / "README.md").write_text("changed\n")
        r = self.runner("run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("non propre", r.stderr)
        self.assertEqual(self.calls(), [])
        (self.repo / "README.md").write_text("hello\n")

    def test_resume_injects_partial_work(self):
        self.git("checkout", "-q", "-b", "feat/x")
        (self.repo / "README.md").write_text("partial\n")
        self.add_task("01-a.md")
        r = self.runner("run", "--resume")
        self.assertEqual(r.returncode, 0, r.stderr)  # le faux agent commite le travail partiel
        self.assertIn("REPRISE", self.calls()[0]["prompt"])
        self.assertIn("README.md", self.calls()[0]["prompt"])

    def test_dry_run_changes_nothing(self):
        self.add_task("01-a.md", max_model="sonnet")
        (self.repo / ".agent-tasks/todo/02-off.md.disabled").write_text("x")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("haiku -> sonnet", r.stdout)
        self.assertNotIn("02-off", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.git("branch", "--show-current").strip(), "main")

    def test_batch_filter(self):
        self.add_task("01-a.md", batch="one")
        self.add_task("02-b.md", batch="two")
        r = self.runner("run", "--batch", "two")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.listing("done"), ["02-b.md"])
        self.assertEqual(self.listing("todo"), ["01-a.md"])

    def test_invalid_task_fails_fast(self):
        (self.repo / ".agent-tasks/todo/01-bad.md").write_text("---\nid: x\n---\nbody\n")
        r = self.runner("run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("branch", r.stderr)

    def test_batch_merge_no_ff_after_all_tasks(self):
        for n in ("01-a.md", "02-b.md"):
            self.add_task(n, batch="b", branch="refactor/b", merge_strategy="auto_no_ff")
        r = self.runner("run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.git("branch", "--show-current").strip(), "main")
        self.assertNotIn("refactor/b", self.git("branch"))
        self.assertEqual(len(self.git("log", "--merges", "--oneline").splitlines()), 1)
        self.assertTrue((self.repo / "work-1.txt").exists())

    def test_batch_not_merged_when_tasks_remain(self):
        self.add_task("01-a.md", batch="b", branch="refactor/b", merge_strategy="auto_ff")
        self.add_task("02-b.md", batch="b", branch="refactor/b", merge_strategy="auto_ff")
        r = self.runner("run", "--no-escalate", FAKE_FAIL_MODELS="haiku")
        self.assertEqual(r.returncode, 1)
        self.assertIn("refactor/b", self.git("branch"))

    def test_status_retry_clean(self):
        self.add_task("01-a.md", escalate=False)
        self.add_task("02-b.md", escalate=False, branch="feat/b")
        self.runner("run", "--continue-on-error", FAKE_FAIL_MODELS="haiku")
        self.assertEqual(self.listing("failed"), ["01-a.md", "02-b.md"])
        st = self.runner("status")
        self.assertEqual(st.returncode, 0)
        self.assertIn("2 en échec", st.stdout)
        self.assertEqual(self.runner("retry", "01-a").returncode, 0)
        self.assertEqual(self.listing("todo"), ["01-a.md"])
        self.assertNotIn("Rapport d'échec", (self.repo / ".agent-tasks/todo/01-a.md").read_text())
        self.assertEqual(self.runner("retry", "nope").returncode, 1)
        self.assertEqual(self.runner("clean").returncode, 0)
        self.assertEqual(list((self.repo / ".agent-tasks/logs").iterdir()), [])


class RoutingTests(Base):
    def setUp(self):
        super().setUp()
        self.init()

    def test_effort_is_passed_to_cli(self):
        self.add_task("01-a.md", model="sonnet", effort="high")
        r = self.runner("run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_invalid_effort_and_fallback(self):
        self.add_task("01-a.md", effort="extreme")
        self.assertIn("effort", self.runner("run").stderr)
        self.add_task("01-a.md", fallback_runner="claude")
        self.assertIn("fallback_runner", self.runner("run").stderr)

    def test_quota_switches_to_fallback_at_equivalent_rank_for_next_tasks_too(self):
        self.add_task("01-a.md", model="sonnet", effort="xhigh", max_model="opus", fallback_runner="agy")
        self.add_task("02-b.md", model="haiku", effort="low", fallback_runner="agy")
        r = self.runner("run", FAKE_QUOTA_MODELS="sonnet")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        calls = [(c.get("runner", "claude"), c["model"]) for c in self.calls()]
        # sonnet -> quota -> argon (fallback_map), xhigh indisponible sur argon -> high ;
        # la tâche 2 part directement sur agy (claude marqué indisponible).
        self.assertEqual(calls, [("claude", "sonnet"), ("agy", "gemini-4-argon-high"),
                                 ("agy", "gemini-3.8-flash-low")])
        self.assertNotIn("--effort", self.calls()[1]["argv"])
        self.assertEqual([row[-1] for row in self.usage_rows()], ["quota", "success", "success"])
        self.assertEqual([row[4] for row in self.usage_rows()], ["claude", "agy", "agy"])

    def test_quota_without_fallback_does_not_escalate(self):
        self.add_task("01-a.md", fallback_runner="none")
        r = self.runner("run", FAKE_QUOTA_MODELS="haiku")
        self.assertEqual(r.returncode, 1)
        self.assertEqual([c["model"] for c in self.calls()], ["haiku"])

    def test_avoid_runner(self):
        self.add_task("01-a.md", model="opus", fallback_runner="agy")
        self.add_task("02-b.md", model="haiku", fallback_runner="none")
        r = self.runner("run", "--dry-run", "--avoid-runner", "claude")
        self.assertIn("01-a.md: [default] agy gemini-4-neon-medium (au lieu de claude)", r.stdout)
        self.assertIn("02-b.md: [default] claude haiku", r.stdout)
        r = self.runner("run", "--avoid-runner", "claude")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([c.get("runner", "claude") for c in self.calls()], ["agy", "claude"])

    def test_agy_catalog_excludes_claude_and_rejects_unknown_models(self):
        r = self.runner("models", "agy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("* gemini-4-neon", r.stdout)
        self.assertNotIn("claude-opus", r.stdout)
        self.add_task("01-a.md", runner="agy", model="claude-opus-5-5")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 1)
        cfg = self.repo / ".agent-tasks/config.toml"
        cfg.write_text(cfg.read_text().replace('"gemini-4-hydrogen", ', '"gemini-4-hydrogen", "ultra", '))
        self.add_task("01-a.md", runner="agy", model="gemini-4-hydrogen", effort="high")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ultra", r.stderr)
        self.assertIn("gemini-4-hydrogen-high -> gemini-4-argon-high -> gemini-4-neon-high", r.stdout)

    def test_models_reports_availability(self):
        r = self.runner("models", FAKE_AGY_LOGGED_OUT="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("runners disponibles : claude\n", r.stdout)
        self.assertIn("runner indisponible : agy (non connecté", r.stdout)

    def test_logged_out_runner_uses_fallback(self):
        self.add_task("01-a.md", fallback_runner="agy")
        r = self.runner("run", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([c.get("runner", "claude") for c in self.calls()], ["agy"])
        self.add_task("02-b.md", fallback_runner="none")
        r = self.runner("run", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("runner claude indisponible (non connecté", r.stderr)

    def test_auto_fallback_without_fallback_runner(self):
        self.add_task("01-a.md", model="sonnet", max_model="opus")  # pas de fallback_runner
        r = self.runner("run", "--dry-run", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertIn("01-a.md: [default] agy gemini-4-argon-medium -> gemini-4-neon-medium "
                      "(au lieu de claude, repli auto)", r.stdout)
        r = self.runner("run", "--dry-run")
        self.assertIn("01-a.md: [default] claude sonnet -> opus (repli auto agy)", r.stdout)
        r = self.runner("run", FAKE_QUOTA_MODELS="sonnet")  # quota : bascule aussi sans fallback_runner
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([(c.get("runner", "claude"), c["model"]) for c in self.calls()],
                         [("claude", "sonnet"), ("agy", "gemini-4-argon-medium")])

    def test_auto_fallback_disabled_by_config(self):
        cfg = self.repo / ".agent-tasks/config.toml"
        self.assertIn("auto_fallback = true", cfg.read_text())
        cfg.write_text(cfg.read_text().replace("auto_fallback = true", "auto_fallback = false"))
        self.add_task("01-a.md")
        r = self.runner("run", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("aucun repli disponible", r.stderr)
        self.assertEqual(self.calls(), [])
        cfg.write_text(cfg.read_text().replace("auto_fallback = false", 'auto_fallback = "oui"'))
        self.assertIn("auto_fallback", self.runner("run", "--dry-run").stderr)

    def test_codex_absent_has_no_impact(self):
        self.add_task("01-a.md")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("codex", r.stdout + r.stderr)
        r = self.runner("models")
        self.assertIn("runners disponibles : agy, claude\n", r.stdout)
        self.assertIn("runner indisponible : codex (non connecté", r.stdout)

    def test_codex_runner_and_auto_fallback_to_first_available(self):
        self.add_task("01-a.md", runner="codex", effort="max", fallback_runner="none")
        r = self.runner("run", FAKE_CODEX_LOGGED_IN="1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        call = self.calls()[0]
        self.assertEqual((call["runner"], call["model"]), ("codex", "gpt-5.4-mini"))
        self.assertIn("model_reasoning_effort=\"xhigh\"", call["argv"])
        self.assertIn("workspace-write", call["argv"])
        row = self.usage_rows()[0]
        self.assertEqual((row[4], row[6], row[7], row[9], row[10], row[-1]), ("codex", "1", "4", "2", "3", "success"))
        # claude déconnecté, agy déconnecté : le repli automatique trouve codex (préférence = ordre de `runners`)
        self.add_task("02-b.md", model="opus")
        r = self.runner("run", "--dry-run", FAKE_CODEX_LOGGED_IN="1", FAKE_CLAUDE_LOGGED_OUT="1",
                        FAKE_AGY_LOGGED_OUT="1")
        self.assertIn("02-b.md: [default] codex gpt-5.4 -> gpt-5.5 (au lieu de claude, repli auto)", r.stdout)

    def test_runners_config_disables_runner(self):
        cfg = self.repo / ".agent-tasks/config.toml"
        cfg.write_text('runners = ["agy"]\n' + cfg.read_text())
        self.add_task("01-a.md", fallback_runner="agy")
        r = self.runner("run", "--dry-run")
        self.assertIn("01-a.md: [default] agy", r.stdout)
        self.assertIn("désactivé par `runners`", r.stderr)
        self.assertIn("runners disponibles : agy\n", self.runner("models").stdout)
        cfg.write_text(cfg.read_text().replace('runners = ["agy"]', 'runners = ["gpt"]'))
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config `runners`", r.stderr)

    def test_map_model(self):
        loader = SourceFileLoader("agent_runner", str(RUNNER))
        spec = importlib.util.spec_from_loader("agent_runner", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        claude, agy = ["haiku", "sonnet", "opus", "fable"], ["flash", "pro", "ultra"]
        self.assertEqual([mod.map_model(m, claude, agy) for m in claude], ["flash", "pro", "pro", "ultra"])
        self.assertEqual([mod.map_model(m, agy, claude) for m in agy], ["haiku", "opus", "fable"])
        self.assertEqual(mod.map_model("sonnet", claude, agy, {"sonnet": "ultra"}), "ultra")


class FrontmatterTests(unittest.TestCase):
    def test_parse(self):
        loader = SourceFileLoader("agent_runner", str(RUNNER))
        spec = importlib.util.spec_from_loader("agent_runner", loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_runner"] = mod
        loader.exec_module(mod)
        meta, body = mod.parse_frontmatter(textwrap.dedent('''\
            ---
            id: 10-x
            # commentaire
            escalate: false
            max_turns: 30
            max_budget_usd: 1.5
            verify_cmd: "a: b && c"
            model: null
            allowed_tools:
              - "Bash(git:*)"
              - Bash(ls:*)
            ---

            ## Objectif
            texte
            '''))
        self.assertEqual(meta, {"id": "10-x", "escalate": False, "max_turns": 30, "max_budget_usd": 1.5,
                                "verify_cmd": "a: b && c", "model": None,
                                "allowed_tools": ["Bash(git:*)", "Bash(ls:*)"]})
        self.assertTrue(body.startswith("## Objectif"))
        with self.assertRaises(ValueError):
            mod.parse_frontmatter("pas de frontmatter")


class MoveLinesTests(unittest.TestCase):
    def run_ml(self, *a):
        return subprocess.run([sys.executable, str(MOVE), *a], capture_output=True, text=True)

    def test_move_append_and_after(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d, "s.txt"), Path(d, "sub/d.txt")
            src.write_text("1\n2\n3\n4\n5")
            r = self.run_ml(str(src), "2", "4", str(dst))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(src.read_text(), "1\n5")
            self.assertEqual(dst.read_text(), "2\n3\n4\n")
            self.run_ml(str(src), "1", "1", str(dst), "--after", "0")
            self.assertEqual(dst.read_text(), "1\n2\n3\n4\n")

    def test_invalid_range_leaves_files_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d, "s.txt"), Path(d, "d.txt")
            src.write_text("a\nb\n")
            r = self.run_ml(str(src), "2", "9", str(dst))
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(src.read_text(), "a\nb\n")
            self.assertFalse(dst.exists())


if __name__ == "__main__":
    unittest.main()
