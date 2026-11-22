"""Détection de la disponibilité des runners et vérification des catalogues de modèles."""

from __future__ import annotations

import shutil
import sys

from agent_runner.adapters import ADAPTERS
from agent_runner.routing import adapter_for
from agent_runner.task import Fail, Task


def enabled_runners(cfg: dict) -> list[str]:
    runners = cfg.get("runners")
    if not isinstance(runners, list) or not all(r in ADAPTERS for r in runners):
        raise Fail(f"config `runners` : liste attendue parmi {', '.join(ADAPTERS)} (reçu {runners!r})")
    return runners


def probe_runners(cfg: dict, runners) -> tuple[dict[str, str | None], dict[str, dict[str, list[str]]]]:
    """Disponibilité de chaque runner (None si utilisable, sinon le motif) et catalogues de modèles.

    Un runner est utilisable s'il est autorisé par `runners`, présent dans le PATH et connecté.
    Interroge les CLI une seule fois (le catalogue agy sert aussi de test de connexion).
    """
    enabled = enabled_runners(cfg)
    status: dict[str, str | None] = {}
    catalogs: dict[str, dict[str, list[str]]] = {}
    for runner in sorted(runners):
        adapter = adapter_for(runner, cfg)
        if runner not in enabled:
            status[runner] = "désactivé par `runners` dans config.toml"
        elif not shutil.which(adapter.executable):
            status[runner] = f"{adapter.executable} introuvable dans le PATH"
        else:
            status[runner], catalog = adapter.probe()
            if catalog is not None:
                catalogs[runner] = catalog
    return status, catalogs


def in_catalog(model: str, catalog: dict) -> bool:
    """Modèle présent au catalogue, tel quel ou sous forme `<famille>-<effort>`."""
    return model in catalog or model.rsplit("-", 1)[0] in catalog


def check_models(cfg: dict, tasks: list[Task], catalogs: dict) -> None:
    """Retire des chaînes les modèles absents du catalogue ; échoue si une tâche en dépend directement."""
    for runner, catalog in catalogs.items():
        chain = cfg["escalation"].get(runner) or []
        missing = [m for m in chain if not in_catalog(m, catalog)]
        if not missing:
            continue
        print(f"avertissement : [escalation].{runner} contient des modèles absents du catalogue "
              f"({', '.join(missing)}) ; ignorés. Voir `agent-runner models`.", file=sys.stderr)
        cfg["escalation"][runner] = [m for m in chain if m not in missing]
        for t in tasks:
            if t.runner == runner and (t.model in missing or t.max_model in missing):
                raise Fail(f"{t.path.name} : modèle {runner} indisponible ({t.model or t.max_model}) ; "
                           "corriger la fiche ou [escalation]")


def check_fallback_map(cfg: dict, tasks: list[Task], catalogs: dict) -> None:
    """Avertit si un repli probable cible un modèle absent de la chaîne ou du catalogue du runner de destination."""
    warned: set[tuple[str, str]] = set()
    for t in tasks:
        alt = t.fallback_runner
        if not alt:
            continue
        chain = cfg["escalation"].get(alt) or []
        explicit = cfg.get("fallback_map", {}).get(alt) or {}
        for m in filter(None, (t.model, t.max_model)):
            target = explicit.get(m)
            if target is None:
                continue
            problem = ("absent de [escalation]." + alt if target not in chain
                       else "absent du catalogue" if alt in catalogs and not in_catalog(target, catalogs[alt])
                       else "")
            if problem and (alt, target) not in warned:
                warned.add((alt, target))
                print(f"avertissement : [fallback_map.{alt}] {m!r} -> {target!r} : {problem} "
                      f"(rang équivalent utilisé à défaut)", file=sys.stderr)
