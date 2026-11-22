"""Commande agent-runner merge."""
from __future__ import annotations

from pathlib import Path

from agent_runner.config import load_config
from agent_runner.frontmatter import parse_frontmatter
from agent_runner.gitutil import branch_exists, repo_root
from agent_runner.merge import build_merge_message, merge_batch_branch
from agent_runner.task import Fail, TASKS_DIR, Task, parse_task
from agent_runner.ui import _ansi


def _pending_for_batch(tasks_dir: Path, batch: str) -> list[str]:
    remaining = []
    for folder in ("todo", "failed"):
        for p in sorted((tasks_dir / folder).glob("*.md")):
            try:
                meta, _ = parse_frontmatter(p.read_text())
            except ValueError:
                continue
            if meta.get("batch", "default") == batch:
                remaining.append(f"{folder}/{p.name}")
    return remaining


def cmd_merge(args) -> int:
    """Fusionne un ou tous les lots terminés (done/) dans leur base_branch avec message structuré."""
    root = repo_root()
    base = root / TASKS_DIR
    if not base.is_dir():
        raise Fail("`.agent-tasks/` introuvable : lancer `agent-runner init`")
    if args.batch and args.all:
        raise Fail("utiliser --batch ou --all, pas les deux")
    cfg = load_config(root)
    done_tasks = [parse_task(p, cfg) for p in sorted((base / "done").glob("*.md"))]
    if not done_tasks:
        print("Aucune tâche terminée dans done/.")
        return 0
    by_batch: dict[str, list[Task]] = {}
    for t in done_tasks:
        by_batch.setdefault(t.batch, []).append(t)

    if args.all:
        targets: list[str] = []
        for b, b_tasks in by_batch.items():
            last = b_tasks[-1]
            rem = _pending_for_batch(base, b) if not args.force else []
            if rem:
                print(f"lot {b} : ignoré (tâches restantes : {', '.join(rem)})")
                continue
            if last.branch == last.base_branch or not branch_exists(root, last.branch):
                print(f"lot {b} : ignoré (branche {last.branch!r} déjà fusionnée ou absente)")
                continue
            targets.append(b)
        if not targets:
            print("Aucun chantier terminé à fusionner.")
            return 0
    else:
        batch = args.batch
        if not batch:
            if len(by_batch) > 1:
                raise Fail(f"plusieurs lots dans done/ ({', '.join(sorted(by_batch))}) : préciser --batch <nom> ou --all")
            batch = next(iter(by_batch))
        elif batch not in by_batch:
            raise Fail(f"lot {batch!r} introuvable dans done/ (disponibles : {', '.join(sorted(by_batch))})")
        targets = [batch]

    rc = 0
    for batch in targets:
        batch_tasks = by_batch[batch]
        last = batch_tasks[-1]
        msg = build_merge_message(root, batch, batch_tasks, last.branch, last.base_branch) if args.strategy != "ff" else None
        if args.dry_run:
            print(_ansi("1", f"Merge prévu pour le lot {batch!r} ({len(batch_tasks)} tâche(s)) :"))
            print(f"  {last.branch} -> {last.base_branch} (stratégie : {args.strategy}, "
                  f"suppression branche : {'non' if args.no_delete else 'oui'})")
            if msg:
                print(_ansi("2", "\nMessage de commit :\n" + "\n".join(f"  {ln}" for ln in msg.splitlines())))
            if len(targets) > 1:
                print()
            continue
        if merge_batch_branch(
            root, base, batch, last.branch, last.base_branch, args.strategy,
            message=msg, delete_branch=not args.no_delete, force=args.force,
        ) != 0:
            rc = 1
    if rc != 0:
        raise Fail("échec du merge d'au moins un lot")
    return 0
