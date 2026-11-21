"""Fonctions utilitaires d'interaction avec le dépôt Git."""
from __future__ import annotations

from pathlib import Path
import subprocess

from agent_runner.task import Fail


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise Fail(f"git {' '.join(args)} a échoué : {(proc.stderr or proc.stdout).strip()}")
    return proc


def repo_root() -> Path:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise Fail("pas dans un dépôt Git")
    return Path(proc.stdout.strip())


def untracked(root: Path) -> set[str]:
    """Fichiers non suivis et non ignorés (`.agent-tasks/` est ignoré)."""
    out = git("ls-files", "--others", "--exclude-standard", cwd=root, check=False).stdout
    return {ln for ln in out.splitlines() if ln}


def read_tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def dirty(root: Path) -> str:
    return git("status", "--porcelain", "--untracked-files=no", cwd=root).stdout.strip()


def branch_exists(root: Path, name: str) -> bool:
    return git("show-ref", "--verify", "--quiet", f"refs/heads/{name}", cwd=root, check=False).returncode == 0
