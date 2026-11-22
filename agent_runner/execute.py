"""Exécution d'une tâche : appel de l'adaptateur, évaluation C1..C4, journal d'usage."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent_runner.gitutil import branch_exists, dirty, git, read_tail, untracked
from agent_runner.prompt import build_prompt
from agent_runner.routing import adapter_for, candidate_models, level_models, map_model, resolve_task_routing
from agent_runner.task import FAILURE_MARKER, TASKS_DIR, Fail, Task
from agent_runner.ui import _ansi, _fmt_dur
from agent_runner.adapters import RunRequest

USAGE_HEADER = [
    "timestamp", "batch", "id", "try", "runner", "model", "turns", "tokens_in",
    "cache_write", "cache_read", "tokens_out", "cost_usd", "duration_sec", "status",
]


def log_usage(logs: Path, task: Task, k: int, runner: str, model: str, res, status: str) -> None:
    path = logs / "usage.tsv"
    new = not path.exists()
    row = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"), task.batch, task.id, k, runner,
        model, res.turns, res.tokens_in, res.cache_write, res.cache_read, res.tokens_out,
        f"{res.cost_usd:.4f}", f"{res.duration_sec:.0f}", status,
    ]
    with path.open("a") as fh:
        if new:
            fh.write("\t".join(USAGE_HEADER) + "\n")
        fh.write("\t".join(str(c) for c in row) + "\n")


def tail(text: str, n: int = 4000) -> str:
    return text if len(text) <= n else "[...]\n" + text[-n:]


# --------------------------------------------------------------------- Exécution
def evaluate(task: Task, root: Path, res, head_before: str, log_base: Path) -> tuple[str, str, str]:
    """Valide C1..C4. Retourne (statut, motif, extrait de sortie de verify_cmd)."""
    if res.error:  # C1
        code = ("quota" if res.quota_hit else "timeout" if res.timed_out
                else "max_turns" if res.max_turns_hit else "error")
        return code, res.error, ""
    if dirty(root):  # C2
        return "dirty", "fichiers suivis modifiés non commités après l'exécution", ""
    if task.require_commit and git("rev-parse", "HEAD", cwd=root).stdout.strip() == head_before:  # C3
        return "no_commit", "HEAD n'a pas avancé (require_commit=true)", ""
    if task.verify_cmd:  # C4
        try:
            proc = subprocess.run(task.verify_cmd, shell=True, cwd=root, capture_output=True,
                                  text=True, errors="replace", timeout=task.timeout_sec)
            out, rc = (proc.stdout + proc.stderr), proc.returncode
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            rc = None
        Path(f"{log_base}.verify.log").write_text(out)
        if rc != 0:
            why = "timeout" if rc is None else f"code retour {rc}"
            return "verify_failed", f"verify_cmd en échec ({why}) : {task.verify_cmd}", tail(out)
    return "success", "", ""


def execute_task(root: Path, cfg: dict, task: Task, args, allow_dirty: bool, progress: str = "") -> bool:
    tasks_dir = root / TASKS_DIR
    logs = tasks_dir / "logs"
    logs.mkdir(exist_ok=True)

    current = git("branch", "--show-current", cwd=root).stdout.strip()
    is_dirty = dirty(root)
    if is_dirty and not allow_dirty:
        raise Fail("arbre de travail non propre (fichiers suivis modifiés) : commit/stash, ou --resume pour reprendre")
    if is_dirty and current != task.branch:
        raise Fail(f"--resume : l'arbre modifié n'est pas sur {task.branch} (actuellement {current or 'HEAD détaché'})")
    if branch_exists(root, task.branch):
        git("checkout", task.branch, cwd=root)
    else:
        git("checkout", "-b", task.branch, task.base_branch, cwd=root)
    head_before = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    partial = git("status", "--short", cwd=root).stdout.strip() if is_dirty else ""

    unavailable: set[str] = args.unavailable  # partagé entre tâches : un quota atteint vaut pour la suite
    all_unavailable = unavailable | {r for r, why in getattr(args, "status", {}).items() if why}
    runner, routing_reason = resolve_task_routing(
        task, cfg, all_unavailable, args.usage_cache.snapshot,
        set(args.avoid_runner or []), args.preference,
    )
    if runner in all_unavailable:
        raise Fail(f"{task.id} : runner {runner!r} indisponible (quota ou exécutable absent) et aucun "
                   "fallback_runner utilisable")
    effective_effort = task.effort or (level_models(task.level, runner, cfg)[2] if task.level else None)
    queue = candidate_models(task, cfg, args.model, args.no_escalate, runner,
                             windows=args.usage_cache.snapshot.get(runner))
    attempts: list[dict] = []
    prev: dict | None = None
    note = f" (repli de {task.runner})" if runner != task.runner else ""
    print(_ansi("1", f"=== {progress}{task.id}") + _ansi("2", f" [{task.batch}] branche {task.branch}")
          + f" — {_ansi('32', runner)}{note} [{routing_reason}] : {' -> '.join(queue)}")

    k = 0
    while queue:
        model = queue.pop(0)
        k += 1
        if prev and prev["status"] != "quota":
            print(_ansi("36", f"    escalade {prev['model']} -> {model}"))
        adapter = adapter_for(runner, cfg)
        try:
            model_id = adapter.resolve_model(model, effective_effort, args.catalogs.get(runner))
        except ValueError as exc:
            raise Fail(f"{task.id} : {exc}") from exc
        safe = re.sub(r"[^\w.-]", "_", f"{runner}-{model_id}" if runner != task.runner else model_id)
        log_base = logs / f"{task.id}.try{k}-{safe}"
        req = RunRequest(
            prompt=build_prompt(task, root, k, prev, partial if k == 1 else "", head_before),
            model=model_id, cwd=root, allowed_tools=task.allowed_tools, max_turns=task.max_turns,
            timeout_sec=task.timeout_sec, max_budget_usd=task.max_budget_usd,
            permission_mode=cfg["permission"], effort=effective_effort,
            extra_args=list(cfg.get("extra_args", {}).get(runner, [])),
        )
        shown = model_id if model_id != model else model + (f" (effort {effective_effort})" if effective_effort else "")
        print(f"    tentative {k} : {_ansi('1', f'{runner} {shown}')} ...", flush=True)
        untracked_before = untracked(root)
        res = adapter.run(req, Path(f"{log_base}.log"), Path(f"{log_base}.json"))
        status, reason, verify_tail = evaluate(task, root, res, head_before, log_base)
        new_files = sorted(untracked(root) - untracked_before)
        log_usage(logs, task, k, runner, model_id, res, status)
        st_color = "1;32" if status == "success" else "33" if status in ("quota", "max_turns", "timeout") else "1;31"
        metrics = f"(tours={res.turns}, coût=${res.cost_usd:.2f}, {_fmt_dur(res.duration_sec)})"
        print(f"    -> {_ansi(st_color, status)}" + (f" : {reason}" if reason else "") + "  " + _ansi("2", metrics))
        if new_files:
            shown_files = ", ".join(new_files[:10]) + (f" (+{len(new_files) - 10})" if len(new_files) > 10 else "")
            if status in ("max_turns", "timeout", "quota", "error"):
                if not args.continue_on_error:
                    print(_ansi("2", f"    fichiers non suivis en cours (conservés pour --resume) : {shown_files}",
                                sys.stderr), file=sys.stderr)
            else:
                print(_ansi("33", f"    ! fichiers non suivis laissés par l'agent (oubli de `git add` ?) : {shown_files}",
                            sys.stderr), file=sys.stderr)
        attempts.append({"k": k, "model": f"{runner} {model_id}", "status": status, "reason": reason})
        if status == "success":
            shutil.move(str(task.path), str(tasks_dir / "done" / task.path.name))
            return True
        agent_tail = ""
        if status != "verify_failed":  # C1/C2/C3 : sans sortie de verify_cmd, donner la fin du travail de l'agent
            agent_tail = tail(res.result_text.strip() or read_tail(Path(f"{log_base}.log")), 1500)
        prev = {"model": f"{runner} {model_id}", "status": status, "reason": reason,
                "verify_tail": verify_tail, "agent_tail": agent_tail}
        if res.permission_denied:
            print(_ansi("1;31", f"    ! {runner} a refusé une commande en mode headless : passer "
                        '`permission = "bypass"` dans config.toml ou configurer permissions.allow dans settings.json.',
                        sys.stderr), file=sys.stderr)
            break
        if status == "quota":
            # Le fournisseur est saturé : inutile d'escalader chez lui. On bascule sur le repli en
            # reprenant au rang équivalent, et on le marque indisponible pour les tâches suivantes.
            args.last_quota_hit = True
            unavailable.add(runner)
            alt = task.fallback_runner if task.fallback_runner not in unavailable else None
            if alt is None or runner == alt:
                print(_ansi("1;31", f"    quota {runner} atteint, aucun runner de repli disponible.", sys.stderr),
                      file=sys.stderr)
                break
            src = cfg["escalation"].get(runner) or []
            dst = cfg["escalation"].get(alt) or []
            start = map_model(model, src, dst, cfg.get("fallback_map", {}).get(alt))
            queue = candidate_models(task, cfg, args.model, args.no_escalate, alt, start=start,
                                     windows=args.usage_cache.snapshot.get(alt))
            print(_ansi("33", f"    bascule {runner} -> {alt} : {' -> '.join(queue)}"))
            runner = alt

    # Échec définitif
    if not args.continue_on_error:
        print(_ansi("1;31", f"    ÉCHEC de {task.id} : tâche laissée dans todo/ avec l'état partiel (--resume pour reprendre).",
                    sys.stderr), file=sys.stderr)
        return False
    git("reset", "--hard", head_before, cwd=root)
    git("clean", "-fd", "-e", TASKS_DIR, cwd=root)
    report = [f"\n{FAILURE_MARKER}", "## Rapport d'échec (agent-runner)", ""]
    for a in attempts:
        report.append(f"- Tentative {a['k']} ({a['model']}) : {a['status']} — {a['reason']}")
    if prev and prev.get("verify_tail"):
        report += ["", "Dernière sortie de verify_cmd :", "```", prev["verify_tail"], "```"]
    with task.path.open("a") as fh:
        fh.write("\n".join(report) + "\n")
    shutil.move(str(task.path), str(tasks_dir / "failed" / task.path.name))
    print(_ansi("1;31", f"    ÉCHEC de {task.id} : arbre réinitialisé, tâche déplacée vers failed/.", sys.stderr),
          file=sys.stderr)
    return False
