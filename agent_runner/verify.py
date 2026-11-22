"""Exécution de `verify_cmd` dans son propre groupe de processus, avec délai dédié."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

KILL_GRACE_SEC = 3  # délai laissé au groupe après SIGTERM avant SIGKILL
DRAIN_SEC = 5  # attente maximale de la sortie restante après la mise à mort du groupe


def _killpg(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_verify(cmd: str, cwd: Path, timeout_sec: int) -> tuple[str, int | None]:
    """Lance `cmd` (shell) et retourne (sortie stdout+stderr, code retour ; None si délai dépassé).

    Le shell et tous ses descendants (test qui boucle, serveur lancé par le script…) forment un
    groupe de processus dédié, entièrement tué à l'expiration du délai : aucun orphelin ne survit
    pour fausser la tentative suivante.
    """
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout_sec)
        return out or "", proc.returncode
    except subprocess.TimeoutExpired:
        _killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=KILL_GRACE_SEC)
        except subprocess.TimeoutExpired:
            pass
        _killpg(proc.pid, signal.SIGKILL)  # descendants éventuellement restés vivants
        try:
            out, _ = proc.communicate(timeout=DRAIN_SEC)
        except subprocess.TimeoutExpired as exc:  # un descendant a quitté le groupe et garde le tube ouvert
            out = exc.output if isinstance(exc.output, str) else (exc.output or b"").decode(errors="replace")
        return out or "", None
