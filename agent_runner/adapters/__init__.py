"""Registre des adaptateurs de CLI d'agents."""

from __future__ import annotations

from .agy import AgyAdapter
from .base import Adapter, AttemptResult, RunRequest
from .claude import ClaudeAdapter
from .codex import CodexAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    "claude": ClaudeAdapter,
    "agy": AgyAdapter,
    "codex": CodexAdapter,  # optionnel : sans effet s'il n'est pas installé
}


def get_adapter(name: str, exclude: list[str] | None = None) -> Adapter:
    try:
        return ADAPTERS[name](exclude)
    except KeyError:
        raise ValueError(f"runner inconnu : {name!r} (attendu : {', '.join(ADAPTERS)})") from None


__all__ = ["ADAPTERS", "Adapter", "AttemptResult", "RunRequest", "get_adapter"]
