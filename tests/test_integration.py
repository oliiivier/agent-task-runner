"""Tests d'intégration : dépôts Git temporaires + faux CLI `claude` placé dans le PATH."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
    print(json.dumps({"type": "assistant", "message": {"id": f"m{n}-{i}",
                      "usage": {"input_tokens": 4, "output_tokens": 2}}}), flush=True)
    if os.environ.get("FAKE_SLOW"):
        import time; time.sleep(0.2)
if os.environ.get("FAKE_CREATE_TASK_FILE"):
    open(os.environ["FAKE_CREATE_TASK_FILE"], "w").write(
        "---\nid: 01-step\nbatch: alpha\nbranch: feat/alpha\nlevel: N1\n---\n## Objectif\nEtape 1.\n"
    )
elif model in csv("FAKE_FAIL_MODELS"):
    pass  # ne produit rien : pas de commit
else:
    open(f"work-{n}.txt", "w").write(model)
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"feat: work {n} by {model}"], check=True)
if model in csv("FAKE_UNTRACKED_MODELS"):
    open("stray.txt", "w").write("x")
if model in csv("FAKE_DIRTY_MODELS"):
    open("README.md", "a").write("dirty\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
                  "result": os.environ.get("FAKE_RESULT_TEXT", "ok"), "total_cost_usd": 0.01,
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
if os.environ.get("FAKE_AGY_HEADLESS_DENIED") == "1" and "--mode" in argv:
    print('a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.',
          file=sys.stderr)
    print(json.dumps({"result": "blocked", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 1}}))
    sys.exit(0)
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
            "FAKE_CLAUDE_USAGE": "",
            "FAKE_AGY_USAGE": "",
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

    def test_init_update_config_preserves_existing_and_is_idempotent(self):
        self.init()
        cfg_path = self.repo / ".agent-tasks/config.toml"
        cfg_path.write_text('main_branch = "develop"\ndefault_max_turns = 25\n')
        r1 = self.runner("init", "--update-config")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("config.toml complété", r1.stdout)
        txt = cfg_path.read_text()
        self.assertIn('main_branch = "develop"', txt)
        self.assertIn("default_max_turns = 25", txt)
        self.assertIn("[routing.level_runner]", txt)
        self.assertIn("[routing.quota_threshold]", txt)
        self.assertIn("[routing]", txt)
        r2 = self.runner("init", "--update-config")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("config.toml déjà à jour", r2.stdout)
        self.assertEqual(cfg_path.read_text().count("[routing.level_runner]"), 1)

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
        (self.repo / ".agent-tasks/logs").mkdir(parents=True, exist_ok=True)
        (self.repo / ".agent-tasks/logs/01-a.try1-haiku.verify.log").write_text("error in build.log\n")
        self.add_task("01-a.md", verify_cmd="true")
        r = self.runner("run", "--resume")
        self.assertEqual(r.returncode, 0, r.stderr)  # le faux agent commite le travail partiel
        prompt = self.calls()[0]["prompt"]
        self.assertIn("REPRISE", prompt)
        self.assertIn("README.md", prompt)
        self.assertIn(".agent-tasks/logs/01-a.try1-haiku.verify.log", prompt)
        self.assertIn("quiet", prompt)

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
        self.assertIn("[default]", r.stdout)
        self.assertIn("01-a.md  agy     gemini-4-neon-medium  (au lieu de claude)", r.stdout)
        self.assertIn("02-b.md  claude  haiku", r.stdout)
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

    def test_models_reports_capabilities_and_metadata(self):
        r = self.runner("models")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("capacités        : allowed_tools: oui | max_turns: oui | max_budget: oui | timeout: oui", r.stdout)
        self.assertIn("capacités        : allowed_tools: non | max_turns: non | max_budget: non | timeout: oui", r.stdout)
        self.assertIn("* haiku", r.stdout)
        self.assertIn("[N1   | 200k | $   ]", r.stdout)
        self.assertIn("* gemini-3.8-flash", r.stdout)
        self.assertIn("[N1   | 1M+  | $   ]", r.stdout)

    def test_models_reports_usage_windows(self):
        fake_usage = json.dumps([
            {"name": "session (5h)", "utilization": 42.0, "reset_delay": "2h15m"},
            {"name": "semaine (7j)", "utilization": 78.0, "reset_delay": "3j 5h"},
        ])
        r = self.runner("models", FAKE_AGY_USAGE=fake_usage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fenêtres d'usage : session (5h): 42% (reset dans 2h15m) | semaine (7j): 78% (reset dans 3j 5h)", r.stdout)

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
        self.assertIn("01-a.md  agy     gemini-4-argon-medium -> gemini-4-neon-medium  "
                      "(au lieu de claude, repli auto)", r.stdout)
        r = self.runner("run", "--dry-run")
        self.assertIn("01-a.md  claude  sonnet -> opus  (repli auto agy)", r.stdout)
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
        self.assertIn('codex avec permission = "accept"', r.stderr)
        call = self.calls()[0]
        self.assertEqual((call["runner"], call["model"]), ("codex", "gpt-5.6-luna"))
        self.assertIn('model_reasoning_effort="max"', call["argv"])
        self.assertIn("workspace-write", call["argv"])
        row = self.usage_rows()[0]
        self.assertEqual((row[4], row[6], row[7], row[9], row[10], row[-1]), ("codex", "1", "4", "2", "3", "success"))
        # claude déconnecté, agy déconnecté : le repli automatique trouve codex (préférence = ordre de `runners`)
        self.add_task("02-b.md", model="opus")
        r = self.runner("run", "--dry-run", FAKE_CODEX_LOGGED_IN="1", FAKE_CLAUDE_LOGGED_OUT="1",
                        FAKE_AGY_LOGGED_OUT="1")
        self.assertIn("02-b.md  codex   gpt-5.6-terra -> gpt-6-luna  (au lieu de claude, repli auto)", r.stdout)

    def test_runners_config_disables_runner(self):
        cfg = self.repo / ".agent-tasks/config.toml"
        cfg.write_text('runners = ["agy"]\n' + cfg.read_text())
        self.add_task("01-a.md", fallback_runner="agy")
        r = self.runner("run", "--dry-run")
        self.assertIn("[default]", r.stdout)
        self.assertIn("01-a.md  agy", r.stdout)
        self.assertIn("désactivé par `runners`", r.stderr)
        self.assertIn("runners disponibles : agy\n", self.runner("models").stdout)
        cfg.write_text(cfg.read_text().replace('runners = ["agy"]', 'runners = ["gpt"]'))
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config `runners`", r.stderr)

    def test_level_routing_in_dry_run(self):
        self.add_task("01-n1.md", level="N1")
        self.add_task("02-n2.md", level="N2")
        self.add_task("03-n3.md", level="N3")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("01-n1.md  agy     gemini-3.8-flash-low -> gemini-4-hydrogen-low", r.stdout)
        self.assertIn("02-n2.md  agy     gemini-4-hydrogen-medium -> gemini-4-argon-medium", r.stdout)
        self.assertIn("03-n3.md  claude  opus -> fable", r.stdout)
        self.assertIn("[niveau N1, usage inconnu]", r.stdout)
        self.assertIn("[niveau N3, usage inconnu]", r.stdout)


class ParsingRobustnessTests(unittest.TestCase):
    def test_agy_extracts_multiline_json_after_noise(self):
        sys.path.insert(0, str(RUNNER.parent.parent))
        from agent_runner.adapters.agy import _extract_json
        out = 'Info: loading\n{"note": "bruit"}\nwarning\n' + json.dumps(
            {"result": "ok", "usage": {"input_tokens": 3}}, indent=2) + "\ntrailing"
        self.assertEqual(_extract_json(out)["usage"], {"input_tokens": 3})
        self.assertIsNone(_extract_json("pas de json"))


class ImprovementTests(Base):
    def test_status_with_header_only_usage(self):
        self.init()
        (self.repo / ".agent-tasks/logs").mkdir(exist_ok=True)
        (self.repo / ".agent-tasks/logs/usage.tsv").write_text("timestamp\tbatch\tid\n")
        r = self.runner("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_dry_run_unknown_agy_model_is_clean_error(self):
        self.init()
        self.add_task("01-a.md", runner="agy", model="gemini-inconnu")
        r = self.runner("run", "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_batch_validation(self):
        self.init()
        self.add_task("01-a.md", batch="b", branch="feat/b", merge_strategy="auto_no_ff")
        self.add_task("02-b.md", batch="b", branch="feat/b")  # merge_strategy omis : incohérent
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("merge_strategy", r.stderr)
        self.assertIn("lot 'b'", r.stderr)

    def test_branch_equal_base_with_merge_rejected(self):
        self.init()
        self.add_task("01-a.md", branch="main", merge_strategy="auto_ff")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("branch == base_branch", r.stderr)

    def test_missing_base_branch_rejected(self):
        self.init()
        self.add_task("01-a.md", base_branch="nope")
        r = self.runner("run", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("base_branch 'nope' introuvable", r.stderr)

    def test_escalation_prompt_lists_previous_commits(self):
        self.init()
        self.add_task("01-a.md")
        self.runner("run", FAKE_DIRTY_MODELS="haiku")
        prompts = [c["prompt"] for c in self.calls()]
        self.assertGreaterEqual(len(prompts), 2)
        self.assertIn("Commits déjà créés", prompts[1])
        self.assertIn("feat: work 1 by haiku", prompts[1])
        self.assertIn("Dernière sortie de l'agent", prompts[1])

    def test_untracked_files_warning(self):
        self.init()
        self.add_task("01-a.md")
        r = self.runner("run", FAKE_UNTRACKED_MODELS="haiku")
        self.assertIn("fichiers non suivis", r.stderr)
        self.assertIn("stray.txt", r.stderr)

    def test_max_turns_keeps_tokens(self):
        self.init()
        self.add_task("01-a.md", max_turns=1, escalate=False, fallback_runner="none")
        self.runner("run", FAKE_TURNS="6", FAKE_SLOW="1")
        row = self.usage_rows()[0]
        self.assertEqual(row[13], "max_turns")
        self.assertGreater(int(row[7]), 0)  # tokens_in récupérés des événements assistant

    def test_allowed_tools_fallback_flag_and_strict(self):
        self.init()
        self.add_task("01-a.md")
        r = self.runner("run", "--dry-run")
        self.assertIn("sans restriction d'outils", r.stdout)
        cfg = self.repo / ".agent-tasks/config.toml"
        cfg.write_text("strict_allowed_tools = true\n" + cfg.read_text())
        r = self.runner("run", "--dry-run")
        self.assertNotIn("sans restriction d'outils", r.stdout)
        self.assertNotIn("repli", r.stdout)

    def test_agy_headless_permission_denied_stops_escalation(self):
        self.init()
        self.add_task("01-a.md", runner="agy", model="gemini-3.8-flash", max_model="gemini-4-hydrogen")
        r = self.runner("run", FAKE_AGY_HEADLESS_DENIED="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn('permission = "bypass"', r.stderr)
        self.assertEqual(len(self.calls()), 1)  # n'escalade pas inutilement chez agy


class RobustnessTests(Base):
    def test_post_result_grace_reaps_hung_child(self):
        import time as _time
        from unittest import mock
        from agent_runner.adapters import base as adapter_base
        from agent_runner.adapters.base import RunRequest
        from agent_runner.adapters.claude import ClaudeAdapter

        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            script = cwd / "hung_cli.py"
            script.write_text(
                "import json, sys, time\n"
                "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, "
                "'num_turns': 1, 'result': 'done', 'total_cost_usd': 0.01, "
                "'usage': {'input_tokens': 1, 'output_tokens': 1}}), flush=True)\n"
                "time.sleep(30)\n"
            )

            class HungClaude(ClaudeAdapter):
                def build_command(self, req):
                    return [sys.executable, str(script)]

            req = RunRequest(prompt="p", model="haiku", cwd=cwd, timeout_sec=30)
            with mock.patch.object(adapter_base, "POST_RESULT_GRACE_SEC", 0.2):
                t0 = _time.monotonic()
                res = HungClaude().run(req, cwd / "run.log", cwd / "run.json")
                elapsed = _time.monotonic() - t0
            self.assertLess(elapsed, 6.0)
            self.assertFalse(res.timed_out)
            self.assertTrue(res.has_result)
            self.assertEqual(res.error, "")

    def test_max_turns_with_untracked_files_shows_real_cause_first(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from types import SimpleNamespace
        from unittest import mock
        from agent_runner.adapters.base import AttemptResult
        from agent_runner.config import load_config
        from agent_runner.execute import execute_task
        from agent_runner.queue import UsageCache, load_queue

        self.init()
        self.add_task("01-a.md", max_turns=1, escalate=False, fallback_runner="none")
        cfg = load_config(self.repo)
        task = load_queue(self.repo / ".agent-tasks", cfg, None)[0]
        args = SimpleNamespace(
            unavailable=set(), status={}, avoid_runner=[], preference=["claude"],
            model=None, no_escalate=True, catalogs={}, continue_on_error=False,
            usage_cache=UsageCache(cfg, ["claude"], {"claude": None}),
        )

        class FakeMaxTurnsAdapter:
            def resolve_model(self, model, effort, catalog):
                return model

            def run(self, req, log_path, json_path):
                (req.cwd / "wip.txt").write_text("partial")
                log_path.write_text("log\n")
                return AttemptResult(
                    returncode=0, max_turns_hit=True, turns=2,
                    error="plafond de 1 tours atteint, agent interrompu avant de terminer",
                )

        out, err = io.StringIO(), io.StringIO()
        with mock.patch("agent_runner.execute.adapter_for", return_value=FakeMaxTurnsAdapter()):
            with redirect_stdout(out), redirect_stderr(err):
                ok = execute_task(self.repo, cfg, task, args, allow_dirty=False)
        self.assertFalse(ok)
        self.assertIn("plafond de 1 tours atteint", out.getvalue())
        self.assertIn("fichiers non suivis en cours", err.getvalue())
        self.assertIn("wip.txt", err.getvalue())
        self.assertNotIn("oubli de `git add`", err.getvalue())

    def test_claude_usage_windows_sets_last_usage_error_on_http_error(self):
        import urllib.error
        from unittest import mock
        from agent_runner.adapters.claude import ClaudeAdapter

        adapter = ClaudeAdapter()
        http_err = urllib.error.HTTPError("https://api.anthropic.com/api/oauth/usage", 429, "Too Many Requests", {}, None)
        try:
            with mock.patch("agent_runner.adapters.claude.get_claude_oauth_token", return_value="tok"):
                with mock.patch("agent_runner.adapters.claude.urllib.request.urlopen", side_effect=http_err):
                    windows = adapter.get_usage_windows()
        finally:
            http_err.close()
        self.assertIsNone(windows)
        self.assertIsNotNone(adapter.last_usage_error)
        self.assertIn("429", adapter.last_usage_error)


class OperationsTests(Base):
    def test_retry_all(self):
        self.init()
        self.add_task("01-a.md", branch="feat/a", escalate=False)
        self.add_task("02-b.md", branch="feat/b", escalate=False)
        self.runner("run", "--continue-on-error", FAKE_FAIL_MODELS="haiku")
        self.assertEqual(self.listing("failed"), ["01-a.md", "02-b.md"])
        r = self.runner("retry", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.listing("failed"), [])
        self.assertEqual(self.listing("todo"), ["01-a.md", "02-b.md"])
        self.assertNotIn("Rapport d'échec", (self.repo / ".agent-tasks/todo/01-a.md").read_text())
        self.assertIn("Aucune tâche", self.runner("retry", "--all").stdout)
        self.assertEqual(self.runner("retry").returncode, 1)
        self.assertEqual(self.runner("retry", "01-a", "--all").returncode, 1)

    def test_on_finish_cmd(self):
        self.init()
        out = self.repo.parent / "hook.txt"
        cfg = self.repo / ".agent-tasks/config.toml"
        cmd = f'echo "$AGENT_RUNNER_STATUS $AGENT_RUNNER_EXIT_CODE $AGENT_RUNNER_DONE/$AGENT_RUNNER_TOTAL ' \
              f'[$AGENT_RUNNER_FAILED_IDS] batch=$AGENT_RUNNER_BATCH branch=$AGENT_RUNNER_BRANCH" >> {out}'
        cfg.write_text(f"on_finish_cmd = {json.dumps(cmd)}\n" + cfg.read_text())
        self.add_task("01-a.md", branch="feat/a")
        self.add_task("02-b.md", branch="feat/b", escalate=False)
        r = self.runner("run", "--continue-on-error", FAKE_FAIL_MODELS="haiku")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(out.read_text(), "failure 1 1/2 [02-b] batch=default branch=feat/a feat/b\n")
        self.runner("run", "--dry-run")  # pas de hook en dry-run
        self.assertEqual(len(out.read_text().splitlines()), 1)

    def test_on_finish_cmd_failure_is_only_a_warning(self):
        self.init()
        cfg = self.repo / ".agent-tasks/config.toml"
        cfg.write_text('on_finish_cmd = "exit 3"\n' + cfg.read_text())
        self.add_task("01-a.md")
        r = self.runner("run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("on_finish_cmd en échec (code 3)", r.stderr)

    def test_report_lists_attempts_and_filters(self):
        self.init()
        r = self.runner("report")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Aucune exécution journalisée", r.stdout)
        self.add_task("01-a.md", batch="alpha", branch="feat/a")
        self.add_task("02-b.md", batch="beta", branch="feat/b", max_model="sonnet")
        self.assertEqual(self.runner("run", FAKE_FAIL_MODELS="haiku").returncode, 0)
        r = self.runner("report")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("01-a", r.stdout)
        self.assertIn("02-b", r.stdout)
        self.assertIn("Total (4 tentative(s))", r.stdout)
        r_batch = self.runner("report", "--batch", "alpha")
        self.assertIn("01-a", r_batch.stdout)
        self.assertNotIn("02-b", r_batch.stdout)
        r_last = self.runner("report", "--last", "1")
        self.assertIn("Total (1 tentative(s))", r_last.stdout)
        r_json = self.runner("report", "--batch", "alpha", "--json")
        self.assertEqual(r_json.returncode, 0, r_json.stderr)
        payload = json.loads(r_json.stdout)
        self.assertEqual(payload["total"]["count"], 2)
        self.assertEqual([a["task_id"] for a in payload["attempts"]], ["01-a", "01-a"])
        self.assertEqual(payload["attempts"][0]["batch"], "alpha")
        self.assertEqual(payload["savings"]["by_level"], {"N1": 1, "N2": 1, "N3": 0})
        self.assertIn("single_session_n3_cost_usd", payload["savings"])
        self.assertIn("avg_overlap_pct", payload["savings"])

    def test_claude_credits_required_429_triggers_quota_fallback(self):
        from agent_runner.adapters.base import Adapter, AttemptResult
        ad = Adapter()
        st = AttemptResult(
            returncode=1, is_error=True, has_result=True,
            result_text="Fable 5.2 requires usage credits.",
            final_json={"api_error_status": 429, "api_error_code": "credits_required",
                        "api_error_type": "rate_limit_error"},
        )
        self.assertTrue(ad.detect_quota(st, []))

    def test_once_and_limit(self):
        self.init()
        self.add_task("01-a.md")
        self.add_task("02-b.md")
        self.add_task("03-c.md")
        r1 = self.runner("run", "--once")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual([p.name for p in (self.repo / ".agent-tasks/done").glob("*.md")], ["01-a.md"])
        self.assertEqual(sorted(p.name for p in (self.repo / ".agent-tasks/todo").glob("*.md")), ["02-b.md", "03-c.md"])
        r2 = self.runner("run", "--limit", "1")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual([p.name for p in (self.repo / ".agent-tasks/todo").glob("*.md")], ["03-c.md"])

    def test_agy_stream_json_counts_turns_and_cache_read(self):
        from agent_runner.adapters.agy import AgyAdapter
        from agent_runner.adapters.base import AttemptResult, RunRequest
        ad = AgyAdapter()
        req = RunRequest(prompt="p", model="gemini-4-hydrogen-medium", cwd=ROOT,
                         allowed_tools=[], max_turns=30, timeout_sec=60, max_budget_usd=None)
        ad.build_command(req)
        st = AttemptResult()
        lines = [
            json.dumps({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "agent_response"}}) + "\n",
            json.dumps({"event": "step_update", "step_update": {"step_index": 2, "state": "DONE", "step_type": "agent_response"}}) + "\n",
            json.dumps({"event": "step_update", "step_update": {"step_index": 3, "state": "DONE", "step_type": "agent_response"}}) + "\n",
            json.dumps({"event": "result", "result": {"status": "SUCCESS", "num_turns": 1, "response": "done",
                        "usage": {"input_tokens": 5000, "output_tokens": 200, "cache_read_tokens": 12000}}}) + "\n",
        ]
        for ln in lines:
            ad.on_line(ln, st, req)
        ad.finalize(st, lines, req)
        self.assertEqual(st.turns, 3)
        self.assertEqual(st.tokens_in, 5000)
        self.assertEqual(st.tokens_out, 200)
        self.assertEqual(st.cache_read, 12000)
        self.assertEqual(st.result_text, "done")

    def test_agy_headless_denied_ignores_tool_output_and_bypass(self):
        from agent_runner.adapters.agy import AgyAdapter
        from agent_runner.adapters.base import AttemptResult, RunRequest
        denied_msg = 'a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.'
        tool_event = json.dumps({
            "event": "step_update",
            "step_update": {"step_index": 1, "state": "DONE", "step_type": "tool",
                            "tool_info": {"name": "run_command", "output": denied_msg}},
        }) + "\n"
        # En mode accept, une sortie d'outil JSON contenant le motif ne doit pas déclencher permission_denied
        ad = AgyAdapter()
        req_accept = RunRequest(prompt="p", model="gemini-3.8-flash-medium", cwd=ROOT,
                                allowed_tools=[], max_turns=10, timeout_sec=60,
                                max_budget_usd=None, permission_mode="accept")
        ad.build_command(req_accept)
        st = AttemptResult()
        ad.on_line(tool_event, st, req_accept)
        ad.finalize(st, [tool_event], req_accept)
        self.assertFalse(st.permission_denied)
        # En mode bypass, même une ligne texte brute ne doit pas déclencher permission_denied
        req_bypass = RunRequest(prompt="p", model="gemini-3.8-flash-medium", cwd=ROOT,
                                allowed_tools=[], max_turns=10, timeout_sec=60,
                                max_budget_usd=None, permission_mode="bypass")
        ad.build_command(req_bypass)
        st2 = AttemptResult()
        ad.finalize(st2, [denied_msg + "\n"], req_bypass)
        self.assertFalse(st2.permission_denied)
        # En mode accept, une ligne texte brute (stderr agy) déclenche bien permission_denied
        ad.build_command(req_accept)
        st3 = AttemptResult()
        ad.finalize(st3, [denied_msg + "\n"], req_accept)
        self.assertTrue(st3.permission_denied)

    def test_merge_batch_and_dry_run(self):
        self.init()
        self.add_task("01-a.md", batch="alpha", branch="feat/alpha", merge_strategy="none")
        self.add_task("02-b.md", batch="alpha", branch="feat/alpha", merge_strategy="none")
        self.assertEqual(self.runner("run").returncode, 0)
        # Dry-run affiche le message de merge déterministe sans toucher aux branches
        dry = self.runner("merge", "--batch", "alpha", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("feat/alpha -> main", dry.stdout)
        self.assertIn("merge(batch): alpha (2 task(s))", dry.stdout)
        self.assertIn("- 01-a: Faire 01-a.", dry.stdout)
        self.assertIn("- 02-b: Faire 02-b.", dry.stdout)
        # Merge réel (--no-ff par défaut) : fusionne dans main et supprime feat/alpha
        res = self.runner("merge", "--batch", "alpha")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("feat/alpha fusionnée dans main (no-ff), branche supprimée", res.stdout)
        self.assertEqual(self.git("branch", "--show-current").strip(), "main")
        log_msg = self.git("log", "-1", "--pretty=%B")
        self.assertIn("merge(batch): alpha (2 task(s))", log_msg)
        self.assertIn("- 01-a: Faire 01-a.", log_msg)
        self.assertEqual(self.git("branch", "--list", "feat/alpha").strip(), "")

    def test_create_plan_questions_rollback_and_success(self):
        self.init()
        plan = self.repo / "plan.md"
        plan.write_text("# Plan\n\nDécouper le module.\n")
        existing = self.repo / ".agent-tasks/todo/00-existing.md"
        existing.write_text("---\nid: 00-existing\nbranch: feat/x\n---\n## Objectif\nExistant.\n")
        # Cas 1 : l'agent pose des questions -> rollback des fiches partielles, conservation de 00-existing.md, code retour 2
        q_reply = (
            "<!-- agent-runner:questions -->\n"
            "1. **Question** : REST ou GraphQL ?\n"
            "   - Recommandation : REST pour rester cohérent.\n"
            "<!-- /agent-runner:questions -->"
        )
        partial_task = self.repo / ".agent-tasks/todo/01-partial.md"
        r_q = self.runner(
            "create-plan", str(plan), "--batch", "alpha", "--branch", "feat/alpha",
            FAKE_RESULT_TEXT=q_reply,
            FAKE_CREATE_TASK_FILE=str(partial_task),
        )
        self.assertEqual(r_q.returncode, 2, r_q.stderr)
        self.assertIn("REST ou GraphQL", r_q.stdout)
        self.assertIn("Prompt à exécuter en session interactive", r_q.stdout)
        self.assertEqual([p.name for p in (self.repo / ".agent-tasks/todo").glob("*.md")], ["00-existing.md"])

        # Cas 2 : création réussie avec plusieurs documents (cartographie + plan) et validation
        carto = self.repo / "cartographie.md"
        carto.write_text("# Cartographie\n\nModules A et B.\n")
        good_task = self.repo / ".agent-tasks/todo/01-step.md"
        r_ok = self.runner(
            "create-plan", str(carto), str(plan), "--batch", "alpha", "--branch", "feat/alpha",
            FAKE_CREATE_TASK_FILE=str(good_task),
        )
        self.assertEqual(r_ok.returncode, 0, r_ok.stderr)
        self.assertIn("1 fiche(s) créée(s) et validée(s)", r_ok.stdout)
        self.assertTrue(good_task.exists())

    def test_merge_all_skips_incomplete_and_already_merged(self):
        self.init()
        # Lot alpha : complet
        self.add_task("01-a.md", batch="alpha", branch="feat/alpha", merge_strategy="none")
        # Lot beta : complet mais branche déjà fusionnée/supprimée
        self.add_task("02-b.md", batch="beta", branch="feat/beta", merge_strategy="none")
        # Lot gamma : 1 tâche exécutée + 1 tâche encore en todo (non terminé)
        self.add_task("03-c.md", batch="gamma", branch="feat/gamma", merge_strategy="none")
        self.assertEqual(self.runner("run").returncode, 0)
        self.add_task("04-d.md", batch="gamma", branch="feat/gamma", merge_strategy="none")
        # Fusionne beta manuellement pour simuler un lot déjà fusionné
        self.assertEqual(self.runner("merge", "--batch", "beta").returncode, 0)
        # Sans --all ni --batch, plusieurs lots dans done/ demandent --batch ou --all
        err = self.runner("merge")
        self.assertEqual(err.returncode, 1)
        self.assertIn("--all", err.stderr)
        # merge --all ne fusionne que alpha, et ignore beta (déjà fusionné) et gamma (incomplet)
        res = self.runner("merge", "--all")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("lot beta : ignoré", res.stdout)
        self.assertIn("lot gamma : ignoré", res.stdout)
        self.assertIn("feat/alpha fusionnée dans main", res.stdout)
        self.assertEqual(self.git("branch", "--list", "feat/alpha").strip(), "")
        self.assertNotEqual(self.git("branch", "--list", "feat/gamma").strip(), "")


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
