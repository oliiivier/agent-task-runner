"""Utilitaires de formatage UI pour le terminal."""
from __future__ import annotations

import os
import sys


def _ansi(code: str, text: str, stream=None) -> str:
    """Applique un style ANSI uniquement sur un terminal interactif (et sans NO_COLOR)."""
    target = stream or sys.stdout
    if target.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{text}\033[0m"
    return text


def _fmt_int(n: int | float) -> str:
    return f"{int(n):,}".replace(",", " ")


def _fmt_dur(sec: float) -> str:
    s = int(sec)
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"
