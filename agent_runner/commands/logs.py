"""Commande logs : affichage et streaming des journaux d'exécution."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from agent_runner.frontmatter import parse_frontmatter
from agent_runner.gitutil import repo_root
from agent_runner.logs import (
    Style,
    collect_batch_task_ids,
    find_matching_logs,
    render_logs_cold,
    stream_logs,
)
from agent_runner.task import TASKS_DIR, Fail
from agent_runner.ui import _ansi


def cmd_logs(args) -> int:
    """Affiche ou suit en temps réel les journaux d'exécution formatés pour un humain."""
    logs_dir: Path | None = None
    batch_ids: set[str] | None = None
    follow_new = True
    target = args.target

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            raise Fail(f"fichier de log introuvable : {args.file}")
        files = [path]
        follow_new = False
    else:
        root = repo_root()
        base = root / TASKS_DIR
        if not base.is_dir():
            raise Fail("`.agent-tasks/` introuvable : lancer `agent-runner init`")
        logs_dir = base / "logs"
        if target and Path(target).is_file():
            files = [Path(target)]
            follow_new = False
        else:
            if args.batch:
                batch_ids = collect_batch_task_ids(base, args.batch, parse_frontmatter)
                if not batch_ids:
                    raise Fail(f"aucune tâche trouvée pour le lot {args.batch!r}")
            files = find_matching_logs(logs_dir, target=target, batch_ids=batch_ids)
            if not args.batch and not target and len(files) > 1:
                files = [files[-1]]
            if not files and not args.stream:
                scope = f" pour le lot {args.batch!r}" if args.batch else (f" pour {target!r}" if target else "")
                print(_ansi("2", f"Aucun journal trouvé{scope}."))
                return 0

    use_pager = bool(not args.stream and not args.no_pager and sys.stdout.isatty())
    use_color = bool(not args.no_color and not os.environ.get("NO_COLOR") and (use_pager or sys.stdout.isatty()))
    style = Style(use_color)

    if args.stream:
        if not files:
            print(style.dim("En attente du prochain fichier de log..."), flush=True)
        try:
            stream_logs(
                files,
                logs_dir,
                target=target,
                batch_ids=batch_ids,
                follow_new=follow_new,
                style=style,
                raw=args.raw,
                include_verify=args.verify,
            )
        except KeyboardInterrupt:
            print()
            return 0
    else:
        render_logs_cold(
            files,
            style,
            raw=args.raw,
            include_verify=args.verify,
            use_pager=use_pager,
        )
    return 0
