"""Configuration par défaut et chargement des fichiers de configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib

from agent_runner.adapters import ADAPTERS
from agent_runner.task import TASKS_DIR, Fail

DEFAULTS: dict = {
    "main_branch": "main",
    "default_runner": "claude",
    "runners": list(ADAPTERS),
    # Fiche sans `fallback_runner` : repli automatique sur l'autre runner (`fallback_runner: none` l'interdit).
    "auto_fallback": True,  # runners autorisés ; un runner absent de la liste est traité comme indisponible
    "default_verify_cmd": "",
    "default_allowed_tools": [
        "Bash(git:*)", "Bash(move-lines:*)", "Bash(file-outline:*)", "Bash(ls:*)",
        "Bash(grep:*)", "Bash(wc:*)", "Bash(mkdir:*)", "Bash(cd:*)",
    ],
    "default_max_turns": 35,
    "default_timeout_sec": 900,
    "permission": "accept",  # "accept" (acceptEdits) | "bypass"
    # true : pas de repli automatique vers un runner qui n'applique pas `allowed_tools` (agy, codex).
    "strict_allowed_tools": False,
    # Commande shell lancée à la fin d'un `run` (notification, webhook) ; voir run_on_finish.
    "on_finish_cmd": "",
    "escalation": {adapter: list(cls.default_chain) for adapter, cls in ADAPTERS.items()},
    # Modèles qu'un CLI ne doit jamais utiliser (motifs fnmatch) : Claude via agy a un quota très réduit.
    "exclude_models": {"agy": ["claude-*"]},
    # Équivalences explicites lors d'une bascule vers un runner de repli (clé = runner de destination).
    # À défaut, transposition au rang relatif dans la chaîne, arrondie vers le haut.
    "fallback_map": {
        "agy": {"haiku": "gemini-3.8-flash", "sonnet": "gemini-4-argon", "opus": "gemini-4-neon",
                "fable": "gemini-4-neon"},
        "claude": {"gemini-3.8-flash": "haiku", "gemini-4-hydrogen": "sonnet", "gemini-4-argon": "sonnet",
                   "gemini-4-neon": "opus"},
    },
    "extra_args": {},
    "routing": {
        "level_runner": {"N1": "agy", "N2": "agy", "N3": "claude"},
        "quota_threshold": {"claude": 80.0, "agy": 80.0, "codex": 80.0},
        "model_quota_threshold": {"argon": 60.0},
        "reset_grace_min": 20,
        "usage_refresh_sec": 300,
        "usage_refresh_tasks": 5,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(root: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for path in (xdg / "agent-task-runner" / "config.toml", root / TASKS_DIR / "config.toml"):
        if path.is_file():
            try:
                cfg = _merge(cfg, tomllib.loads(path.read_text()))
            except tomllib.TOMLDecodeError as exc:
                raise Fail(f"{path} : TOML invalide : {exc}") from exc
    return cfg
