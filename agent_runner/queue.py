"""File de tâches : chargement, validation et cliché d'usage des runners."""

from __future__ import annotations

import time
from pathlib import Path

from agent_runner.gitutil import branch_exists
from agent_runner.routing import adapter_for
from agent_runner.task import Fail, Task, parse_task


class UsageCache:
    """Cliché d'usage avec rafraîchissement paresseux (temps, N tâches ou erreur de quota)."""

    def __init__(self, cfg: dict, runners: list[str], status: dict[str, str | None]) -> None:
        self.cfg = cfg
        self.runners = [r for r in runners if not status.get(r)]
        routing_cfg = cfg.get("routing", {})
        self.min_interval = float(routing_cfg.get("usage_refresh_sec", 300))
        self.task_interval = int(routing_cfg.get("usage_refresh_tasks", 5))
        self.snapshot: dict[str, list | None] = {}
        self.last_fetch: float = 0.0
        self.tasks_since_fetch: int = 0
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force:
            if (now - self.last_fetch) < self.min_interval or self.tasks_since_fetch < self.task_interval:
                return
        for r in self.runners:
            try:
                self.snapshot[r] = adapter_for(r, self.cfg).get_usage_windows()
            except Exception:
                self.snapshot[r] = None
        self.last_fetch = now
        self.tasks_since_fetch = 0

    def on_task_done(self, quota_hit: bool = False) -> None:
        self.tasks_since_fetch += 1
        if quota_hit:
            if (time.monotonic() - self.last_fetch) >= min(30.0, self.min_interval):
                self.refresh(force=True)
        else:
            self.refresh(force=False)


def load_queue(tasks_dir: Path, cfg: dict, batch: str | None) -> list[Task]:
    tasks = [parse_task(p, cfg) for p in sorted((tasks_dir / "todo").glob("*.md"))]
    seen: set[str] = set()
    for t in tasks:
        if t.id in seen:
            raise Fail(f"id de tâche en double : {t.id}")
        seen.add(t.id)
    return [t for t in tasks if batch is None or t.batch == batch]


def validate_queue(root: Path, cfg: dict, tasks: list[Task]) -> None:
    """Cohérence des tâches et des lots, vérifiée avant toute exécution (y compris --dry-run)."""
    errors: list[str] = []
    by_batch: dict[str, list[Task]] = {}
    created: set[str] = set()  # branches créées par les tâches précédentes de la file
    for t in tasks:
        name = t.path.name
        if t.merge_strategy != "none" and t.branch == t.base_branch:
            errors.append(f"{name} : branch == base_branch ({t.branch}) incompatible avec merge_strategy={t.merge_strategy}")
        if not (branch_exists(root, t.base_branch) or t.base_branch in created) and not branch_exists(root, t.branch):
            errors.append(f"{name} : base_branch {t.base_branch!r} introuvable")
        created.add(t.branch)
        by_batch.setdefault(t.batch, []).append(t)
    for batch, members in by_batch.items():
        if all(m.merge_strategy == "none" for m in members):
            continue  # lot sans fusion automatique : les tâches restent indépendantes
        ref = members[0]
        for t in members[1:]:
            for attr in ("branch", "base_branch", "merge_strategy"):
                if getattr(t, attr) != getattr(ref, attr):
                    errors.append(f"{t.path.name} : {attr}={getattr(t, attr)!r} diffère de {ref.path.name} "
                                  f"({getattr(ref, attr)!r}) dans le lot {batch!r} (fusion automatique)")
    if errors:
        raise Fail("incohérences dans la file :\n  " + "\n  ".join(errors))


def apply_strict_allowed_tools(cfg: dict, tasks: list[Task]) -> None:
    """strict_allowed_tools : pas de repli vers un runner qui ignore `allowed_tools`."""
    if not cfg.get("strict_allowed_tools"):
        return
    for t in tasks:
        alt = t.fallback_runner
        if not (t.allowed_tools and alt and adapter_for(t.runner, cfg).supports_allowed_tools
                and not adapter_for(alt, cfg).supports_allowed_tools):
            continue
        if not t.fallback_auto:
            raise Fail(f"{t.path.name} : fallback_runner {alt!r} n'applique pas allowed_tools "
                       "(strict_allowed_tools = true)")
        t.fallback_runner, t.fallback_auto = None, False
