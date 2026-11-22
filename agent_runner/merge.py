"""Fusion et finalisation des branches de lots."""

from __future__ import annotations

import sys
from pathlib import Path

from agent_runner.frontmatter import parse_frontmatter
from agent_runner.gitutil import branch_exists, dirty, git
from agent_runner.task import TASKS_DIR, Task


def _extract_objective(body: str) -> str:
    """Extrait la première ligne utile sous `## Objectif` dans le corps d'une fiche."""
    in_obj = False
    for line in body.splitlines():
        s = line.strip()
        if s.lower().startswith("## objectif"):
            in_obj = True
            continue
        if in_obj:
            if s.startswith("## "):
                break
            if s and s != "...":
                return s.lstrip("- ").strip()
    return ""


def build_merge_message(root: Path, batch: str, tasks: list[Task], branch: str, base_branch: str) -> str:
    """Construit un message de merge déterministe et structuré à partir des fiches terminées."""
    lines = [f"merge(batch): {batch} ({len(tasks)} task(s))", ""]
    for t in tasks:
        obj = _extract_objective(t.body)
        lines.append(f"- {t.id}: {obj}" if obj else f"- {t.id}")
    commits = git("log", "--oneline", f"{base_branch}..{branch}", cwd=root, check=False).stdout.strip()
    if commits:
        n_commits = len(commits.splitlines())
        lines += ["", f"Branch: {branch} -> {base_branch} ({n_commits} commit(s))"]
    return "\n".join(lines)


def merge_batch_branch(
    root: Path, tasks_dir: Path, batch: str, branch: str, base_branch: str, strategy: str,
    message: str | None = None, delete_branch: bool = True, force: bool = False,
) -> int:
    """Fusionne `branch` dans `base_branch` après vérification du lot et propreté de l'arbre."""
    if not force:
        remaining = []
        for folder in ("todo", "failed"):
            for p in sorted((tasks_dir / folder).glob("*.md")):
                try:
                    meta, _ = parse_frontmatter(p.read_text())
                except ValueError:
                    continue
                if meta.get("batch", "default") == batch:
                    remaining.append(f"{folder}/{p.name}")
        if remaining:
            print(f"lot {batch} : non finalisé, tâches restantes : {', '.join(remaining)}", file=sys.stderr)
            return 1
    if dirty(root):
        print(f"lot {batch} : merge impossible, arbre non propre", file=sys.stderr)
        return 1
    if branch == base_branch:
        print(f"lot {batch} : branch == base_branch ({branch}), rien à fusionner.", file=sys.stderr)
        return 1
    if not branch_exists(root, branch):
        print(f"lot {batch} : branche {branch!r} introuvable.", file=sys.stderr)
        return 1
    if not branch_exists(root, base_branch):
        print(f"lot {batch} : base_branch {base_branch!r} introuvable.", file=sys.stderr)
        return 1

    git("checkout", base_branch, cwd=root)
    if strategy in ("ff", "auto_ff"):
        proc = git("merge", "--ff-only", branch, cwd=root, check=False)
    elif strategy == "squash":
        proc = git("merge", "--squash", branch, cwd=root, check=False)
        if proc.returncode == 0:
            proc = git("commit", "-m", message or f"merge(batch): {batch}", cwd=root, check=False)
    else:  # no-ff | auto_no_ff
        cmd = ["merge", "--no-ff"] + (["-m", message] if message else ["--no-edit"]) + [branch]
        proc = git(*cmd, cwd=root, check=False)

    if proc.returncode != 0:
        git("merge", "--abort", cwd=root, check=False)
        if strategy == "squash":
            git("reset", "--hard", "HEAD", cwd=root, check=False)
        print(f"lot {batch} : merge {strategy} de {branch} dans {base_branch} impossible "
              f"(branche conservée) : {(proc.stderr or proc.stdout).strip()}", file=sys.stderr)
        return 1

    if delete_branch:
        git("branch", "-D" if strategy == "squash" else "-d", branch, cwd=root)
        print(f"lot {batch} : {branch} fusionnée dans {base_branch} ({strategy}), branche supprimée.")
    else:
        print(f"lot {batch} : {branch} fusionnée dans {base_branch} ({strategy}), branche conservée.")
    return 0


def finalize_batches(root: Path, cfg: dict, completed: list[Task]) -> int:
    tasks_dir = root / TASKS_DIR
    by_batch: dict[str, list[Task]] = {}
    for t in completed:
        by_batch.setdefault(t.batch, []).append(t)
    rc = 0
    for batch, batch_tasks in by_batch.items():
        t = batch_tasks[-1]
        if t.merge_strategy == "none":
            continue
        remaining = []
        for folder in ("todo", "failed"):
            for p in sorted((tasks_dir / folder).glob("*.md")):
                try:
                    meta, _ = parse_frontmatter(p.read_text())
                except ValueError:
                    continue
                if meta.get("batch", "default") == batch:
                    remaining.append(f"{folder}/{p.name}")
        if remaining:
            print(f"lot {batch} : non finalisé, tâches restantes : {', '.join(remaining)}")
            continue
        msg = build_merge_message(root, batch, batch_tasks, t.branch, t.base_branch) if t.merge_strategy == "auto_no_ff" else None
        if merge_batch_branch(root, tasks_dir, batch, t.branch, t.base_branch, t.merge_strategy,
                              message=msg, delete_branch=True, force=True) != 0:
            rc = 1
    return rc
