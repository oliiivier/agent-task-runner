"""Routage des tâches vers les runners et sélection des modèles selon les quotas."""

from __future__ import annotations

from agent_runner.adapters import get_adapter
from agent_runner.task import Fail, Task


def adapter_for(runner: str, cfg: dict):
    return get_adapter(runner, cfg.get("exclude_models", {}).get(runner))


def map_model(model: str | None, src: list[str], dst: list[str], explicit: dict | None = None) -> str | None:
    """Transpose un modèle d'une chaîne d'escalade vers une autre.

    `explicit` (`[fallback_map.<destination>]`) prime ; sinon rang relatif équivalent, arrondi vers le
    haut : une bascule ne doit pas rétrograder la tâche.
    """
    if model is None or not dst:
        return None
    target = (explicit or {}).get(model)
    if target in dst:
        return target
    if model not in src or len(src) < 2:
        return dst[0]
    rank = src.index(model) * (len(dst) - 1) / (len(src) - 1)
    return dst[min(len(dst) - 1, int(rank + 0.5))]


def level_models(level: str | None, runner: str, cfg: dict) -> tuple[str | None, str | None, str | None]:
    """Retourne (model, max_model, effort_defaut) sur `runner` pour un niveau N1/N2/N3."""
    if not level:
        return None, None, None
    chain = cfg["escalation"].get(runner) or []
    if not chain:
        return None, None, None
    adapter = adapter_for(runner, cfg)
    efforts = {"N1": "low", "N2": "medium", "N3": "high"}
    effort = efforts.get(level)
    matching = [m for m in chain if (adapter.get_model_info(m) or {}).get("level", "").startswith(level)]
    if matching:
        start = matching[0]
        idx = chain.index(start)
        end = chain[min(idx + 1, len(chain) - 1)]
        return start, end, effort
    if level == "N1":
        return chain[0], chain[min(1, len(chain) - 1)], effort
    if level == "N2":
        mid = len(chain) // 2
        return chain[mid], chain[min(mid + 1, len(chain) - 1)], effort
    return chain[-1], chain[-1], effort


def model_quota_status(runner: str, model: str, windows: list | None, cfg: dict) -> tuple[bool, bool]:
    """Retourne (skip_start, skip_all) si le modèle appartient à un groupe de quota dédié (ex. argon)."""
    if not windows:
        return False, False
    info = adapter_for(runner, cfg).get_model_info(model) or {}
    group = info.get("quota_group")
    if not group:
        return False, False
    routing_cfg = cfg.get("routing", {})
    grace_min = float(routing_cfg.get("reset_grace_min", 20.0))
    start_thresh = float(routing_cfg.get("model_quota_threshold", {}).get(group, 60.0))
    hard_thresh = float(routing_cfg.get("quota_threshold", {}).get(runner, 80.0))
    matching = [
        w for w in windows
        if w.name.lower().startswith(group.lower())
        and not (w.reset_minutes is not None and w.reset_minutes <= grace_min and w.utilization < 95.0)
    ]
    if not matching:
        return False, False
    peak = max(w.utilization for w in matching)
    return peak >= start_thresh, peak >= hard_thresh


def candidate_models(task: Task, cfg: dict, forced: str | None, no_escalate: bool,
                     runner: str | None = None, start: str | None = None,
                     windows: list | None = None) -> list[str]:
    """Chaîne de modèles à essayer pour `task` sur `runner` (par défaut son runner nominal).

    Sur le runner de repli, `model`, `max_model` et `--model` sont transposés par `map_model` ;
    `start` permet de reprendre la chaîne au rang déjà atteint lors d'une bascule.
    """
    runner = runner or task.runner
    chain = cfg["escalation"].get(runner) or []
    if not chain:
        raise Fail(f"aucune chaîne d'escalade définie pour le runner {runner!r}")
    model, max_model = task.model, task.max_model
    if task.level and not model:
        lvl_model, lvl_max, _ = level_models(task.level, runner, cfg)
        model = lvl_model
        max_model = max_model or lvl_max
    elif runner != task.runner:
        src = cfg["escalation"].get(task.runner) or []
        explicit = cfg.get("fallback_map", {}).get(runner)
        model, max_model, forced = (map_model(m, src, chain, explicit) for m in (model, max_model, forced))
    effective_start = start or forced or model or chain[0]
    if no_escalate or not task.escalate or effective_start not in chain:
        return [effective_start]
    i = chain.index(effective_start)
    j = chain.index(max_model) if max_model in chain else len(chain) - 1
    queue = chain[i : max(i, j) + 1]
    if not task.allow_premium and not forced and not start and not task.model and len(queue) > 1:
        adapter = adapter_for(runner, cfg)
        non_premium = [m for m in queue if not (adapter.get_model_info(m) or {}).get("premium")]
        if non_premium:
            queue = non_premium
    if windows and len(queue) > 1:
        pinned_start = bool(start or forced or task.model)
        kept = [m for m in queue if not model_quota_status(runner, m, windows, cfg)[1]]
        if kept:
            queue = kept
        if not pinned_start and len(queue) > 1 and model_quota_status(runner, queue[0], windows, cfg)[0]:
            queue = queue[1:]
    return queue


def pick_runner(task: Task, unavailable: set[str], avoid: set[str]) -> str:
    """Runner effectif : le nominal, sauf s'il est indisponible/évité et que le repli est utilisable."""
    alt = task.fallback_runner
    if alt and alt not in unavailable and (task.runner in unavailable or (task.runner in avoid and alt not in avoid)):
        return alt
    return task.runner


def check_runner_quota(runner: str, windows: list | None, cfg: dict) -> tuple[bool, str]:
    """Évalue si un runner dépasse son seuil de quota.

    Les fenêtres dédiées à un groupe de modèle (ex. `argon (7j)`) n'invalident pas tout le runner
    tant que les fenêtres globales (session/semaine) restent disponibles.
    """
    if not windows:
        return False, "usage inconnu"
    routing_cfg = cfg.get("routing", {})
    threshold = float(routing_cfg.get("quota_threshold", {}).get(runner, 80.0))
    grace_min = float(routing_cfg.get("reset_grace_min", 20.0))
    model_groups = {
        g.lower() for g in routing_cfg.get("model_quota_threshold", {})
    }
    active = []
    relaxed = False
    for w in windows:
        if w.reset_minutes is not None and w.reset_minutes <= grace_min and w.utilization < 95.0:
            relaxed = True
            continue
        active.append(w)
    if not active:
        return False, "reset imminent" if relaxed else "usage inconnu"
    runner_windows = [w for w in active if not any(w.name.lower().startswith(g) for g in model_groups)]
    pool = runner_windows or active
    max_w = max(pool, key=lambda w: w.utilization)
    if max_w.utilization >= threshold:
        return True, f"{max_w.name} {max_w.utilization:.0f}% >= {threshold:.0f}%"
    return False, f"{max_w.name} {max_w.utilization:.0f}%"


def resolve_task_routing(
    task: Task,
    cfg: dict,
    unavailable: set[str],
    usage_snapshot: dict[str, list | None],
    avoid: set[str],
    preference_order: list[str],
) -> tuple[str, str]:
    """Résout (runner_effectif, raison) de façon pure.

    Ordre :
    1. Filtre sur les capacités dures (strict_allowed_tools, max_budget_usd).
    2. Runner épinglé (`runner:` explicite) ou préférence par niveau (`[routing.level_runner]`) / défaut.
    3. Seuils de fenêtres d'usage (ignoré si usage indisponible ou runner épinglé).
    """
    candidates = [r for r in preference_order if r not in unavailable and r not in avoid]
    needs_tools = bool(cfg.get("strict_allowed_tools") and task.allowed_tools)
    needs_budget = task.max_budget_usd is not None
    hard_capable = [
        r for r in candidates
        if (not needs_tools or adapter_for(r, cfg).supports_allowed_tools)
        and (not needs_budget or adapter_for(r, cfg).supports_max_budget)
    ]
    pool = hard_capable if hard_capable else candidates

    if task.runner_pinned or (not task.level and not task.prefer_runner):
        base_reason = "pin" if task.runner_pinned else "défaut"
        if task.runner in pool:
            if not task.runner_pinned:
                over, quota_note = check_runner_quota(task.runner, usage_snapshot.get(task.runner), cfg)
                if over and task.fallback_runner and task.fallback_runner in pool:
                    alt_over, _ = check_runner_quota(task.fallback_runner, usage_snapshot.get(task.fallback_runner), cfg)
                    if not alt_over:
                        return task.fallback_runner, f"{base_reason}, bascule ({task.runner}: {quota_note})"
                if quota_note == "usage inconnu":
                    return task.runner, f"{base_reason}, usage inconnu"
            return task.runner, base_reason
        if task.runner in candidates and not hard_capable:
            return task.runner, base_reason
        if needs_tools and not adapter_for(task.runner, cfg).supports_allowed_tools and pool:
            return pool[0], "contrainte allowed_tools"
        if needs_budget and not adapter_for(task.runner, cfg).supports_max_budget and pool:
            return pool[0], "contrainte budget"
        alt = task.fallback_runner
        if alt and alt in pool:
            return alt, f"repli ({task.runner} indisponible)"
        return task.runner, "indisponible"

    level_pref = (
        task.prefer_runner
        or (cfg.get("routing", {}).get("level_runner", {}).get(task.level) if task.level else None)
        or cfg.get("default_runner")
    )
    if level_pref in pool:
        preferred = level_pref
        if task.prefer_runner:
            base_reason = f"préférence {task.prefer_runner}" + (f" ({task.level})" if task.level else "")
        else:
            base_reason = f"niveau {task.level}" if task.level else "défaut"
    elif pool:
        preferred = pool[0]
        if needs_tools and level_pref and not adapter_for(level_pref, cfg).supports_allowed_tools:
            base_reason = "contrainte allowed_tools"
        elif needs_budget and level_pref and not adapter_for(level_pref, cfg).supports_max_budget:
            base_reason = "contrainte budget"
        else:
            base_reason = f"{level_pref} indisponible -> {preferred}"
    else:
        return task.runner, "indisponible"

    over, quota_note = check_runner_quota(preferred, usage_snapshot.get(preferred), cfg)
    if over:
        for alt in pool:
            if alt == preferred:
                continue
            alt_over, _ = check_runner_quota(alt, usage_snapshot.get(alt), cfg)
            if not alt_over:
                return alt, f"{base_reason}, bascule ({preferred}: {quota_note})"
        return preferred, f"{base_reason} ({quota_note}, sans alternative)"
    if quota_note == "usage inconnu":
        return preferred, f"{base_reason}, usage inconnu"
    return preferred, base_reason
