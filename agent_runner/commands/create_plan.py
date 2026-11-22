"""Commande create-plan : planification de documents en tâches atomiques."""

from __future__ import annotations

from pathlib import Path
import re

from agent_runner.adapters import RunRequest
from agent_runner.config import load_config
from agent_runner.gitutil import dirty, git, repo_root
from agent_runner.probe import enabled_runners, probe_runners
from agent_runner.routing import adapter_for, level_models
from agent_runner.task import TASKS_DIR, TOOL_ROOT, Fail, parse_task
from agent_runner.ui import _ansi


def cmd_create_plan(args) -> int:
    """Découpe un ou plusieurs documents (plan, spécification, cartographie) en fiches `.agent-tasks/todo/`."""
    root = repo_root()
    base = root / TASKS_DIR
    if not base.is_dir():
        raise Fail("`.agent-tasks/` introuvable : lancer `agent-runner init`")
    if dirty(root):
        raise Fail("arbre de travail non propre (fichiers suivis modifiés) : commit ou stash avant `create-plan`")
    plan_paths: list[Path] = []
    for raw in args.plans:
        p = Path(raw)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.is_file():
            raise Fail(f"document introuvable : {raw}")
        plan_paths.append(p)

    cfg = load_config(root)
    preference = enabled_runners(cfg)
    statuses, catalogs = probe_runners(cfg, set(preference) | ({args.runner} if args.runner else set()))
    runner = args.runner or cfg["default_runner"]
    if statuses.get(runner):
        avail = [r for r in preference if not statuses.get(r)]
        if args.runner or not avail:
            raise Fail(f"runner {runner!r} indisponible : {statuses.get(runner) or 'aucun runner disponible'}")
        runner = avail[0]

    adapter = adapter_for(runner, cfg)
    model_name = args.model or level_models("N2", runner, cfg)[0] or cfg["escalation"][runner][0]
    effort = args.effort or "medium"
    model_id = adapter.resolve_model(model_name, effort, catalogs.get(runner))

    skill_file = TOOL_ROOT / "skills" / "plan-tasks" / "SKILL.md"
    skill_text = skill_file.read_text() if skill_file.is_file() else ""
    constraints = []
    if args.batch:
        constraints.append(f"- `batch`: `{args.batch}`")
    if args.branch:
        constraints.append(f"- `branch`: `{args.branch}`")
    if args.base_branch:
        constraints.append(f"- `base_branch`: `{args.base_branch}`")
    q_begin, q_end = "<!-- agent-runner:questions -->", "<!-- /agent-runner:questions -->"
    docs_list = ", ".join(f"`{p}`" for p in plan_paths)
    docs_sections = "\n\n".join(f"## Document : {p.name} ({p})\n\n{p.read_text()}" for p in plan_paths)
    prompt = (
        f"Planifie les documents suivants ({docs_list}) en micro-tâches dans `.agent-tasks/todo/` en suivant "
        f"strictement le skill `plan-tasks` ci-dessous.\n\n"
        + ("Contraintes imposées :\n" + "\n".join(constraints) + "\n\n" if constraints else "")
        + "## Protocole en cas d'ambiguïté (OBLIGATOIRE)\n"
        "Si ces documents comportent des ambiguïtés bloquantes ou des choix d'architecture non tranchés "
        "qui t'empêchent de rédiger des fiches autonomes et sûres :\n"
        "- NE CRÉE AUCUNE fiche dans `.agent-tasks/todo/` et ne modifie aucun fichier.\n"
        f"- Réponds uniquement avec un bloc balisé `{q_begin} ... {q_end}` listant "
        "chaque question numérotée suivie de `- Recommandation : ...`.\n\n"
        f"## Instructions plan-tasks\n\n{skill_text}\n\n"
        f"{docs_sections}"
    )

    head_before = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    todo_dir = base / "todo"
    todo_before = set(todo_dir.glob("*.md"))
    logs = base / "logs"
    logs.mkdir(exist_ok=True)
    req = RunRequest(
        prompt=prompt, model=model_id, cwd=root,
        allowed_tools=list(cfg["default_allowed_tools"]) + ["Read", "Write", "Glob", "Grep"],
        max_turns=30, timeout_sec=cfg["default_timeout_sec"], max_budget_usd=None,
        permission_mode=cfg["permission"], effort=effort,
        extra_args=list(cfg.get("extra_args", {}).get(runner, [])),
    )
    label = ", ".join(p.name for p in plan_paths)
    print(_ansi("1", f"create-plan : {label} ({runner}/{model_id}, effort={effort})"))
    res = adapter.run(req, logs / "create-plan.log", logs / "create-plan.json")

    # Garde-fou code source : rollback Git si un fichier suivi/non-suivi hors .agent-tasks/ a été modifié.
    if dirty(root) or git("rev-parse", "HEAD", cwd=root).stdout.strip() != head_before:
        git("reset", "--hard", head_before, cwd=root)
        git("clean", "-fd", "-e", TASKS_DIR, cwd=root)

    def _rollback_todo() -> None:
        for p in set(todo_dir.glob("*.md")) - todo_before:
            p.unlink()

    q_match = re.search(rf"{re.escape(q_begin)}(.*?){re.escape(q_end)}", res.result_text, re.S)
    if q_match:
        _rollback_todo()
        questions = q_match.group(1).strip()
        print(_ansi("1;33", "\ncreate-plan interrompu : l'agent a des questions avant de découper le plan.\n"))
        print(questions)
        opts = " ".join(f"--{k} {v}" for k, v in (("batch", args.batch), ("branch", args.branch), ("base-branch", args.base_branch)) if v)
        print(_ansi("1", "\nPrompt à exécuter en session interactive (Claude / agy) :\n"))
        print(f"Utilise le skill plan-tasks pour découper {' '.join(str(p) for p in plan_paths)} dans .agent-tasks/todo/ "
              + (f"({opts}) " if opts else "")
              + "en tenant compte des réponses suivantes :\n"
              + "\n".join(ln for ln in questions.splitlines() if ln.strip()))
        return 2

    if res.error:
        _rollback_todo()
        raise Fail(f"create-plan : {res.error}")

    new_files = sorted(set(todo_dir.glob("*.md")) - todo_before)
    if not new_files:
        raise Fail("create-plan : aucune fiche créée dans `.agent-tasks/todo/`.")

    tasks = [parse_task(p, cfg) for p in new_files]
    print(_ansi("1;32", f"\n{len(tasks)} fiche(s) créée(s) et validée(s) dans .agent-tasks/todo/ :"))
    for t in tasks:
        print(f"  - {t.path.name}  [level={t.level or '-'} branch={t.branch}]")
    print(_ansi("2", "\nVérifie avec : agent-runner run --dry-run"))
    return 0
