"""Interface commune des adaptateurs CLI d'agents et chaînes d'escalade."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunRequest:
    """Paramètres d'une tentative d'exécution d'un agent."""

    prompt: str
    model: str
    cwd: Path
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 35
    timeout_sec: int = 900
    max_budget_usd: float | None = None
    permission_mode: str = "accept"  # "accept" | "bypass"
    effort: str | None = None  # low | medium | high | xhigh | max (None : défaut du CLI)
    extra_args: list[str] = field(default_factory=list)


@dataclass
class AttemptResult:
    """Résultat normalisé d'une tentative (indépendant du fournisseur)."""

    returncode: int | None = None
    timed_out: bool = False
    max_turns_hit: bool = False
    quota_hit: bool = False  # quota/limite d'usage/surcharge du fournisseur (pas un échec du modèle)
    permission_denied: bool = False  # commande refusée en mode headless (configuration, pas échec du modèle)
    is_error: bool = False
    has_result: bool = False
    turns: int = 0
    tokens_in: int = 0
    cache_write: int = 0
    cache_read: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    result_text: str = ""
    final_json: dict | None = None
    error: str = ""  # motif d'échec C1 lisible (vide si succès)


# Signatures d'indisponibilité du fournisseur (quota, limite d'usage, surcharge). Elles ne sont
# recherchées que dans les messages d'erreur du CLI, jamais dans le travail de l'agent.
QUOTA_RE = re.compile(
    r"usage limit|hit your limit|rate[ _-]?limit|quota|resource[ _]exhausted|overloaded|"
    r"too many requests|\b429\b|\b529\b",
    re.IGNORECASE,
)


TERM_GRACE_SEC = 3  # délai laissé au CLI pour écrire son résumé final après SIGTERM, avant SIGKILL


class Adapter:
    """Classe de base : lance le CLI, journalise le flux, applique timeout et max_turns."""

    name = ""
    executable = ""
    default_chain: list[str] = []
    requires_result = True  # C1 exige un événement/objet de résultat final
    supports_allowed_tools = False  # le CLI applique-t-il `allowed_tools` (liste blanche d'outils) ?
    exclude: list[str] = []  # motifs fnmatch de modèles à ne jamais utiliser via ce CLI

    def __init__(self, exclude: list[str] | None = None) -> None:
        self.exclude = list(exclude or [])

    def build_command(self, req: RunRequest) -> list[str]:
        raise NotImplementedError

    def list_models(self) -> dict[str, list[str]] | None:
        """Catalogue {famille: [efforts disponibles]} interrogé auprès du CLI, ou None s'il n'en expose pas."""
        return None

    def probe(self) -> tuple[str | None, dict[str, list[str]] | None]:
        """(motif d'indisponibilité ou None, catalogue). Suppose l'exécutable présent dans le PATH.

        Par défaut : connexion vérifiée par `auth_problem`, puis catalogue via `list_models`.
        """
        reason = self.auth_problem()
        return reason, (None if reason else self.list_models())

    def auth_problem(self) -> str | None:
        """Motif si le CLI n'est manifestement pas connecté ; None si connecté ou indéterminable."""
        return None

    def resolve_model(self, model: str, effort: str | None, catalog: dict[str, list[str]] | None) -> str:
        """Identifiant réellement passé à `--model` (par défaut : le maillon de la chaîne tel quel)."""
        return model

    def on_line(self, line: str, state: AttemptResult, req: RunRequest) -> bool:
        """Appelé pour chaque ligne de sortie ; retourne True pour interrompre le processus."""
        return False

    def finalize(self, state: AttemptResult, lines: list[str], req: RunRequest) -> None:
        """Complète `state` (tokens, coût, ...) une fois le processus terminé."""

    # ------------------------------------------------------------------
    def run(self, req: RunRequest, log_path: Path, json_path: Path) -> AttemptResult:
        state = AttemptResult()
        lines: list[str] = []
        start = time.monotonic()
        cmd = self.build_command(req)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=req.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            state.error = f"impossible de lancer {cmd[0]} : {exc}"
            state.duration_sec = time.monotonic() - start
            json_path.write_text(json.dumps({"error": state.error}, indent=2))
            log_path.write_text(state.error + "\n")
            return state

        def kill(sig: int = signal.SIGKILL) -> None:
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

        graceful: list[threading.Timer] = []

        def stop() -> None:
            """SIGTERM puis SIGKILL après un court délai : le CLI peut ainsi émettre son résultat (tokens, coût)."""
            kill(signal.SIGTERM)
            hard = threading.Timer(TERM_GRACE_SEC, kill)
            hard.daemon = True
            graceful.append(hard)
            hard.start()

        def on_timeout() -> None:
            state.timed_out = True
            stop()

        timer = threading.Timer(req.timeout_sec, on_timeout)
        timer.daemon = True
        timer.start()
        try:
            with log_path.open("w") as log:
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    lines.append(line)
                    if self.on_line(line, state, req) and not state.max_turns_hit:
                        state.max_turns_hit = True
                        stop()
            proc.wait()
        finally:
            timer.cancel()
            for t in graceful:
                t.cancel()
            kill()  # ne laisse aucun processus orphelin (également sur Ctrl-C)
            proc.wait()

        state.returncode = proc.returncode
        state.duration_sec = time.monotonic() - start
        self.finalize(state, lines, req)
        state.quota_hit = self.detect_quota(state, lines)
        state.error = self._diagnose(state, req)
        payload = state.final_json if state.final_json is not None else {
            "error": state.error or "pas de résultat final",
            "returncode": state.returncode,
        }
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return state

    def detect_quota(self, state: AttemptResult, lines: list[str]) -> bool:
        """Vrai si l'échec provient d'un quota/limite du fournisseur plutôt que du modèle."""
        if state.timed_out or state.max_turns_hit:
            return False
        if not (state.is_error or state.returncode != 0 or not state.has_result):
            return False
        # Texte d'erreur final + lignes non-JSON de fin de sortie (stderr fusionné du CLI).
        haystack = [state.result_text if state.is_error or state.returncode != 0 else ""]
        haystack += [ln for ln in lines[-30:] if not ln.lstrip().startswith("{")]
        return any(QUOTA_RE.search(h) for h in haystack if h)

    def _diagnose(self, state: AttemptResult, req: RunRequest) -> str:
        if state.quota_hit:
            return "quota/limite d'usage du fournisseur atteint (bascule de runner possible)"
        if state.timed_out:
            return f"timeout après {req.timeout_sec}s"
        if state.max_turns_hit:
            return f"max_turns atteint ({req.max_turns})"
        if state.returncode != 0:
            return f"code retour {state.returncode} du CLI"
        if self.requires_result and not state.has_result:
            return "aucun résultat final émis par le CLI"
        if state.is_error:
            return "l'agent a signalé une erreur (is_error=true)" + (
                f" : {state.result_text[:500]}" if state.result_text else ""
            )
        return ""
