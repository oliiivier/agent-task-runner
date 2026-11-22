"""Commandes utilitaires : status, retry, clean."""

from __future__ import annotations

import shutil
import sys

from agent_runner.frontmatter import parse_frontmatter
from agent_runner.gitutil import repo_root
from agent_runner.task import FAILURE_MARKER, TASKS_DIR, Fail
from agent_runner.ui import _ansi, _fmt_int


def cmd_status(args) -> int:
    root = repo_root()
    base = root / TASKS_DIR
    if not base.is_dir():
        raise Fail("`.agent-tasks/` introuvable : lancer `agent-runner init`")
    files = {d: sorted((base / d).glob("*.md")) for d in ("todo", "done", "failed")}
    counts = {d: len(files[d]) for d in files}
    fail_str = _ansi("1;31" if counts["failed"] else "2", f"{counts['failed']} en échec")
    print(f"{'Tâches :':<14} {_ansi('36', str(counts['todo']))} à faire, "
          f"{_ansi('32', str(counts['done']))} terminées, {fail_str}")
    if files["failed"]:
        print(f"{'En échec :':<14} " + ", ".join(p.name for p in files["failed"])
              + _ansi("2", "  (relancer : agent-runner retry <id> ou --all)"))
    usage = base / "logs" / "usage.tsv"
    if not usage.exists():
        print(_ansi("2", "Aucune exécution journalisée."))
        return 0
    rows = [ln.split("\t") for ln in usage.read_text().splitlines()[1:] if ln.strip()]
    per_task: dict[str, int] = {}
    cost = tin = tout = 0.0
    by_model: dict[str, float] = {}
    for r in rows:
        per_task[r[2]] = max(per_task.get(r[2], 0), int(r[3]))
        cost += float(r[11])
        tin += int(r[7]) + int(r[8]) + int(r[9])
        tout += int(r[10])
        by_model[r[5]] = by_model.get(r[5], 0.0) + float(r[11])
    if not rows:
        print(_ansi("2", "Aucune exécution journalisée."))
        return 0
    escalated = sum(1 for n in per_task.values() if n > 1)
    print(f"{'Tentatives :':<14} {len(rows)} sur {len(per_task)} tâches ; escaladées : {escalated} "
          f"({100 * escalated / len(per_task):.0f} %)")
    print(f"{'Tokens :':<14} entrée (cache inclus) {_fmt_int(tin)}, sortie {_fmt_int(tout)}")
    breakdown = ", ".join(f"{m} ${c:.2f}" for m, c in by_model.items())
    print(f"{'Coût cumulé :':<14} {_ansi('1', f'${cost:.2f}')}  " + _ansi("2", f"({breakdown})"))
    return 0


def cmd_retry(args) -> int:
    root = repo_root()
    base = root / TASKS_DIR
    if args.all == bool(args.id):
        raise Fail("retry : indiquer un id ou --all (pas les deux)")
    moved = 0
    for p in sorted((base / "failed").glob("*.md")):
        if not args.all:
            try:
                meta, _ = parse_frontmatter(p.read_text())
            except ValueError:
                meta = {}
            if args.id not in (str(meta.get("id")), p.stem):
                continue
        dest = base / "todo" / p.name
        if dest.exists():
            print(f"{p.name} : déjà présente dans todo/, laissée dans failed/", file=sys.stderr)
            if not args.all:
                return 1
            continue
        text = p.read_text()
        if FAILURE_MARKER in text:
            text = text[: text.index(FAILURE_MARKER)].rstrip("\n") + "\n"
        dest.write_text(text)
        p.unlink()
        print(f"{p.name} remise dans todo/")
        moved += 1
        if not args.all:
            return 0
    if args.all:
        print(f"{moved} tâche(s) remise(s) dans todo/" if moved else "Aucune tâche dans failed/.")
        return 0
    raise Fail(f"tâche {args.id!r} introuvable dans failed/")


def cmd_clean(args) -> int:
    root = repo_root()
    base = root / TASKS_DIR
    targets = ["logs"] + (["done"] if args.all else [])
    for d in targets:
        shutil.rmtree(base / d, ignore_errors=True)
        (base / d).mkdir(exist_ok=True)
    print("supprimé : " + ", ".join(f"{d}/" for d in targets))
    return 0
