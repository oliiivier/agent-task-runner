"""Commande agent-runner run."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from agent_runner.commands.report import _print_savings, _row_to_attempt, summarize_savings
from agent_runner.config import load_config
from agent_runner.execute import execute_task, tail
from agent_runner.gitutil import git, repo_root
from agent_runner.merge import finalize_batches
from agent_runner.probe import check_fallback_map, check_models, enabled_runners, probe_runners
from agent_runner.queue import UsageCache, apply_strict_allowed_tools, load_queue, validate_queue
from agent_runner.routing import adapter_for, candidate_models, level_models, resolve_task_routing
from agent_runner.task import TASKS_DIR, Fail, Task
from agent_runner.ui import _ansi, _fmt_dur

ON_FINISH_TIMEOUT_SEC = 60


def print_dry_run(tasks: list[Task], cfg: dict, args, status: dict[str, str | None], avoid: set[str]) -> None:
    """Affiche le plan d'exécution (--dry-run) regroupé par lot/branche, avec alignement et couleurs TTY."""
    print(_ansi("1", f"Plan d'exécution : {len(tasks)} tâche(s)"))
    name_w = min(max(len(t.path.name) for t in tasks), 36)
    last_group: tuple[str, str, str, str] | None = None
    all_unavailable = args.unavailable | {r for r, why in status.items() if why}
    for t in tasks:
        runner, routing_reason = resolve_task_routing(
            t, cfg, all_unavailable, args.usage_cache.snapshot, avoid, args.preference
        )
        adapter = adapter_for(runner, cfg)
        effective_effort = t.effort or (level_models(t.level, runner, cfg)[2] if t.level else None)
        try:
            chain = [adapter.resolve_model(m, effective_effort, args.catalogs.get(runner))
                     for m in candidate_models(t, cfg, args.model, args.no_escalate, runner,
                                               windows=args.usage_cache.snapshot.get(runner))]
        except ValueError as exc:
            raise Fail(f"{t.id} : {exc}") from exc
        group = (t.batch, t.branch, t.base_branch, t.merge_strategy)
        if group != last_group:
            last_group = group
            print()
            print(_ansi("1;36", f"[{t.batch}]") + f"  branche {_ansi('1', t.branch)} <- {t.base_branch}  "
                  + _ansi("2", f"(merge: {t.merge_strategy})"))
        notes: list[str] = []
        if t.fallback_runner and t.fallback_runner != runner:
            notes.append(_ansi("2", f"(repli {'auto ' if t.fallback_auto else ''}{t.fallback_runner})"))
        elif runner != t.runner:
            notes.append(_ansi("2", f"(au lieu de {t.runner}{', repli auto' if t.fallback_auto else ''})"))
        notes.append(_ansi("2", f"[{routing_reason}]"))
        if runner in args.unavailable:
            notes.append(_ansi("1;31", f"[INDISPONIBLE : {status[runner]}]"))
        if t.allowed_tools:
            if not adapter.supports_allowed_tools:
                notes.append(_ansi("33", "[sans restriction d'outils]"))
            elif t.fallback_runner and not adapter_for(t.fallback_runner, cfg).supports_allowed_tools:
                notes.append(_ansi("33", f"[repli {t.fallback_runner} : sans restriction d'outils]"))
        suffix = ("  " + " ".join(notes)) if notes else ""
        print(f"  {t.path.name:<{name_w}}  {_ansi('32', f'{runner:<6}')}  {' -> '.join(chain)}{suffix}")
        meta = []
        if t.level:
            meta.append(f"level={t.level}")
        if effective_effort:
            meta.append(f"effort={effective_effort}")
        meta.append(f"max_turns={t.max_turns}")
        if t.timeout_sec != cfg["default_timeout_sec"]:
            meta.append(f"timeout={t.timeout_sec}s")
        meta.append(f"verify: {t.verify_cmd or '-'}")
        print(_ansi("2", "    " + "  ".join(meta)))


def cmd_run(args) -> int:
    root = repo_root()
    tasks_dir = root / TASKS_DIR
    if not tasks_dir.is_dir():
        raise Fail("`.agent-tasks/` introuvable : lancer `agent-runner init`")
    if getattr(args, "once", False) and getattr(args, "limit", None) is not None:
        raise Fail("utiliser --once ou --limit, pas les deux")
    limit = 1 if getattr(args, "once", False) else getattr(args, "limit", None)
    if limit is not None and limit < 1:
        raise Fail("--limit doit être >= 1")
    cfg = load_config(root)
    tasks = load_queue(tasks_dir, cfg, args.batch)
    if limit is not None:
        tasks = tasks[:limit]
    if not tasks:
        print("Aucune tâche à exécuter.")
        return 0
    avoid = set(args.avoid_runner or [])
    used = {t.runner for t in tasks} | {t.fallback_runner for t in tasks if t.fallback_runner}
    preference = enabled_runners(cfg)  # ordre de `runners` = préférence pour le repli automatique
    status, args.catalogs = probe_runners(cfg, used | set(preference))
    args.status = status
    args.preference = preference
    args.unavailable = {r for r, why in status.items() if why and r in used}
    for t in tasks:  # repli automatique : premier autre runner réellement utilisable
        if t.fallback_auto:
            t.fallback_runner = next((r for r in preference if r != t.runner and not status[r]), t.fallback_runner)
            if status.get(t.fallback_runner):
                args.unavailable.add(t.fallback_runner)
    for r in sorted(args.unavailable):
        print(f"runner indisponible : {r} ({status[r]})", file=sys.stderr)
    check_models(cfg, tasks, args.catalogs)
    apply_strict_allowed_tools(cfg, tasks)
    validate_queue(root, cfg, tasks)
    check_fallback_map(cfg, tasks, args.catalogs)
    args.usage_cache = UsageCache(cfg, preference, status)
    all_unavailable = args.unavailable | {r for r, why in status.items() if why}
    if cfg.get("permission") != "bypass":
        routed = {
            resolve_task_routing(t, cfg, all_unavailable, args.usage_cache.snapshot, avoid, preference)[0]
            for t in tasks if t.require_commit
        }
        if "agy" in routed:
            print(_ansi("33", 'avertissement : agy avec permission = "accept" bloque les commandes shell '
                        '(git commit) sans règles permissions.allow dans settings.json ; utiliser '
                        'permission = "bypass" si besoin.', sys.stderr), file=sys.stderr)
        if "codex" in routed:
            print(_ansi("33", 'avertissement : codex avec permission = "accept" monte .git/ en lecture seule '
                        '(--sandbox workspace-write), bloquant git commit ; utiliser '
                        'permission = "bypass" si besoin.', sys.stderr), file=sys.stderr)
    if args.dry_run:
        print_dry_run(tasks, cfg, args, status, avoid)
        return 0
    if git("check-ignore", "-q", TASKS_DIR, cwd=root, check=False).returncode != 0:
        raise Fail(".agent-tasks n'est pas ignoré par Git : relancer `agent-runner init`")
    for t in tasks:
        runner, _ = resolve_task_routing(t, cfg, all_unavailable, args.usage_cache.snapshot, avoid, preference)
        if runner in all_unavailable:
            raise Fail(f"{t.id} : runner {runner} indisponible ({status[runner]}) "
                       "et aucun repli disponible (fallback_runner: none, auto_fallback = false "
                       "ou runner de repli lui aussi indisponible)")
    completed: list[Task] = []
    failed: list[Task] = []
    rc: int | None = None
    t0 = time.monotonic()
    usage_file = root / TASKS_DIR / "logs" / "usage.tsv"
    usage_start = len(usage_file.read_text().splitlines()) if usage_file.exists() else 1
    try:
        total = len(tasks)
        for i, task in enumerate(tasks):
            args.last_quota_hit = False
            ok = execute_task(root, cfg, task, args, allow_dirty=args.resume and i == 0,
                              progress=f"[{i + 1}/{total}] ")
            args.usage_cache.on_task_done(quota_hit=args.last_quota_hit)
            if ok:
                completed.append(task)
            else:
                failed.append(task)
                if not args.continue_on_error:
                    rc = 1
                    return rc
        rc = finalize_batches(root, cfg, completed)
        rc = 1 if failed else rc
        return rc
    finally:  # également sur erreur fatale ou Ctrl-C (rc reste None)
        if completed or failed:
            parts = [_ansi("1;32", f"{len(completed)}/{len(tasks)} réussie(s)")]
            if failed:
                parts.append(_ansi("1;31", f"{len(failed)} en échec"))
            parts.append(_ansi("2", _fmt_dur(time.monotonic() - t0)))
            print("\n" + _ansi("1", "Bilan :") + " " + ", ".join(parts))
            if usage_file.exists():
                run_rows = [ln.split("\t") for ln in usage_file.read_text().splitlines()[usage_start:] if ln.strip()]
                if run_rows:
                    _print_savings(summarize_savings([_row_to_attempt(r) for r in run_rows], cfg))
        run_on_finish(root, cfg, rc, tasks, completed, failed)


def run_on_finish(root: Path, cfg: dict, rc: int | None, tasks: list[Task],
                  completed: list[Task], failed: list[Task]) -> None:
    """Lance `on_finish_cmd` (shell, depuis la racine du dépôt) avec le bilan dans l'environnement.

    AGENT_RUNNER_STATUS (success | failure | error), AGENT_RUNNER_EXIT_CODE (vide si interrompu),
    AGENT_RUNNER_TOTAL / _DONE / _FAILED (compteurs), AGENT_RUNNER_DONE_IDS / _FAILED_IDS (ids séparés
    par des espaces), AGENT_RUNNER_BATCH / _BRANCH (valeurs uniques séparées par des espaces),
    AGENT_RUNNER_REPO. Un échec du hook n'affecte pas le code retour du runner.
    """
    cmd = cfg.get("on_finish_cmd") or ""
    if not isinstance(cmd, str):
        print(f"avertissement : config `on_finish_cmd` : chaîne attendue (reçu {cmd!r})", file=sys.stderr)
        return
    if not cmd.strip():
        return
    batches = list(dict.fromkeys(t.batch for t in tasks if t.batch))
    branches = list(dict.fromkeys(t.branch for t in tasks if t.branch))
    env = {
        **os.environ,
        "AGENT_RUNNER_STATUS": "error" if rc is None else "success" if rc == 0 else "failure",
        "AGENT_RUNNER_EXIT_CODE": "" if rc is None else str(rc),
        "AGENT_RUNNER_TOTAL": str(len(tasks)),
        "AGENT_RUNNER_DONE": str(len(completed)),
        "AGENT_RUNNER_FAILED": str(len(failed)),
        "AGENT_RUNNER_DONE_IDS": " ".join(t.id for t in completed),
        "AGENT_RUNNER_FAILED_IDS": " ".join(t.id for t in failed),
        "AGENT_RUNNER_BATCH": " ".join(batches),
        "AGENT_RUNNER_BRANCH": " ".join(branches),
        "AGENT_RUNNER_REPO": str(root),
    }
    try:
        proc = subprocess.run(cmd, shell=True, cwd=root, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace", timeout=ON_FINISH_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        print(f"avertissement : on_finish_cmd interrompu après {ON_FINISH_TIMEOUT_SEC}s", file=sys.stderr)
        return
    except OSError as exc:
        print(f"avertissement : on_finish_cmd : {exc}", file=sys.stderr)
        return
    if proc.returncode != 0:
        print(f"avertissement : on_finish_cmd en échec (code {proc.returncode}) : "
              f"{tail((proc.stderr or proc.stdout).strip(), 500)}", file=sys.stderr)
