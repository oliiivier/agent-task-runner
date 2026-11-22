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
    session_id: str | None = None  # conserve la session sous cet identifiant (reprise ultérieure possible)
    resume: bool = False  # True : reprend la session `session_id` au lieu d'en créer une


@dataclass
class WindowQuota:
    """Fenêtre de consommation (session glissante, semaine, quota modèle)."""

    name: str  # ex. "session (5h)", "semaine (7j)", "sonnet (7j)"
    utilization: float  # pourcentage utilisé (0.0 à 100.0)
    resets_at: str | None = None
    reset_delay: str = ""  # ex. "2h15m", "3j 4h", "imminent"
    reset_minutes: float | None = None  # minutes restantes avant reset


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
    r"too many requests|usage credits|credits_required|model_requires_usage_credits|\b429\b|\b529\b",
    re.IGNORECASE,
)


TERM_GRACE_SEC = 3  # délai laissé au CLI pour écrire son résumé final après SIGTERM, avant SIGKILL
POST_RESULT_GRACE_SEC = 10  # délai de grâce après réception d'un `result` avant de tuer une sous-tâche orpheline


class Adapter:
    """Classe de base : lance le CLI, journalise le flux, applique timeout et max_turns."""

    name = ""
    executable = ""
    default_chain: list[str] = []
    requires_result = True  # C1 exige un événement/objet de résultat final
    supports_allowed_tools = False  # le CLI applique-t-il `allowed_tools` (liste blanche d'outils) ?
    supports_resume = False  # le CLI sait-il reprendre une session (`session_id` + `resume`) ?
    supports_max_turns = False  # le CLI applique-t-il ou permet-il d'appliquer max_turns ?
    supports_max_budget = False  # le CLI supporte-t-il un plafond financier max_budget_usd ?
    supports_timeout = True  # timeout surveillé par le runner
    recommended_usage = ""
    baseline_model: str = ""
    model_metadata: dict[str, dict[str, Any]] = {}
    exclude: list[str] = []  # motifs fnmatch de modèles à ne jamais utiliser via ce CLI

    def __init__(self, exclude: list[str] | None = None) -> None:
        self.exclude = list(exclude or [])
        self.last_usage_error: str | None = None

    def get_model_info(self, model: str) -> dict[str, Any] | None:
        """Retourne les métadonnées {'level', 'context', 'cost', 'desc', 'pricing'} pour un modèle ou None."""
        return self.model_metadata.get(model)

    def get_model_pricing(self, model: str) -> tuple[float, float, float, float] | None:
        """Retourne (in, cache_write, cache_read, out) en $/MTok pour `model`, ou None."""
        info = self.get_model_info(model)
        pricing = info.get("pricing") if info else None
        return tuple(pricing) if pricing else None

    @staticmethod
    def estimate_cost(pricing: tuple[float, float, float, float],
                      tokens_in: int, cache_write: int, cache_read: int, tokens_out: int) -> float:
        p_in, p_cw, p_cr, p_out = pricing
        return (tokens_in * p_in + cache_write * p_cw + cache_read * p_cr + tokens_out * p_out) / 1_000_000.0

    def get_usage_windows(self) -> list[WindowQuota] | None:
        """Fenêtres de consommation (session 5h, semaine 7j) si le fournisseur les expose."""
        return None

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
        reaped_after_result = False
        post_result_timer: threading.Timer | None = None

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

        def on_post_result_hang() -> None:
            nonlocal reaped_after_result
            reaped_after_result = True
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
                    elif state.final_json is not None and post_result_timer is None and not state.max_turns_hit:
                        post_result_timer = threading.Timer(POST_RESULT_GRACE_SEC, on_post_result_hang)
                        post_result_timer.daemon = True
                        post_result_timer.start()
            proc.wait()
        finally:
            timer.cancel()
            if post_result_timer is not None:
                post_result_timer.cancel()
            for t in graceful:
                t.cancel()
            kill()  # ne laisse aucun processus orphelin (également sur Ctrl-C)
            proc.wait()

        state.returncode = proc.returncode
        state.duration_sec = time.monotonic() - start
        self.finalize(state, lines, req)
        if reaped_after_result and state.has_result and state.returncode is not None and state.returncode < 0:
            state.returncode = 0
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
        # Texte d'erreur final + champs d'erreur JSON + lignes non-JSON de fin de sortie (stderr fusionné).
        haystack = [state.result_text if state.is_error or state.returncode != 0 else ""]
        if state.final_json and (state.is_error or state.returncode != 0):
            for k in ("api_error_status", "api_error_type", "api_error_code", "api_error", "error"):
                if state.final_json.get(k) is not None:
                    haystack.append(str(state.final_json[k]))
        haystack += [ln for ln in lines[-30:] if not ln.lstrip().startswith("{")]
        return any(QUOTA_RE.search(h) for h in haystack if h)

    def _diagnose(self, state: AttemptResult, req: RunRequest) -> str:
        if state.quota_hit:
            return "quota/limite d'usage du fournisseur atteint (bascule de runner possible)"
        if state.timed_out:
            return f"délai de {req.timeout_sec}s dépassé, agent interrompu avant de terminer"
        if state.max_turns_hit:
            return f"plafond de {req.max_turns} tours atteint, agent interrompu avant de terminer"
        if state.returncode != 0:
            return f"code retour {state.returncode} du CLI"
        if self.requires_result and not state.has_result:
            return "aucun résultat final émis par le CLI"
        if state.is_error:
            return "l'agent a signalé une erreur (is_error=true)" + (
                f" : {state.result_text[:500]}" if state.result_text else ""
            )
        return ""
