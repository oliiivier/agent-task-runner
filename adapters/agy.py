"""Adaptateur pour `agy` (mode `--print`, sortie `--output-format json`).

`agy` n'expose ni liste blanche d'outils, ni plafond de tours, ni budget : `allowed_tools`,
`max_turns` (non vérifiable) et `max_budget_usd` ne sont donc pas transmis. Le timeout est
appliqué par le runner (et relayé via `--print-timeout`). Le format JSON exact n'étant pas
documenté dans `agy --help`, l'analyse des métriques est tolérante : à défaut, les compteurs
restent à 0 et seuls le code retour et `is_error` décident du succès (C1).
"""

from __future__ import annotations

import json
import re
import subprocess
from fnmatch import fnmatch

from .base import Adapter, AttemptResult, RunRequest

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
_SUFFIX = re.compile(r"^(?P<family>.+)-(?P<effort>" + "|".join(EFFORT_LEVELS) + r")$")
DEFAULT_EFFORT = "medium"
_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._]*-[a-z0-9._-]+")


def split_model_id(model_id: str) -> tuple[str, str | None]:
    """`gemini-4-neon-high` -> (`gemini-4-neon`, `high`) ; identifiant sans suffixe -> (id, None)."""
    m = _SUFFIX.match(model_id)
    return (m["family"], m["effort"]) if m else (model_id, None)


def parse_models(output: str, exclude: list[str] | None = None) -> dict[str, list[str]]:
    """Analyse la sortie de `agy models` (`<id>\t<libellé>` par ligne) en {famille: [efforts triés]}."""
    catalog: dict[str, list[str]] = {}
    for line in output.splitlines():
        tokens = line.split(None, 1)
        # Ligne de modèle : `<id>  <libellé>` ; ignore les messages (`Fetching available models...`).
        if len(tokens) != 2 or not _MODEL_ID.fullmatch(tokens[0]):
            continue
        model_id = tokens[0]
        if any(fnmatch(model_id, pat) for pat in exclude or []):
            continue
        family, effort = split_model_id(model_id)
        efforts = catalog.setdefault(family, [])
        if effort and effort not in efforts:
            efforts.append(effort)
    for efforts in catalog.values():
        efforts.sort(key=EFFORT_LEVELS.index)
    return catalog


def nearest_effort(wanted: str, available: list[str]) -> str:
    """Effort disponible le plus proche ; à égalité, le plus élevé (ne pas rétrograder)."""
    w = EFFORT_LEVELS.index(wanted)
    return min(available, key=lambda e: (abs(EFFORT_LEVELS.index(e) - w), -EFFORT_LEVELS.index(e)))


def _first(d: dict, *keys, default=0):
    for key in keys:
        if d.get(key) is not None:
            return d[key]
    return default


class AgyAdapter(Adapter):
    name = "agy"
    executable = "agy"
    # Familles du moins cher au plus puissant ; l'effort est ajouté en suffixe (`<famille>-<effort>`).
    default_chain = ["gemini-3.8-flash", "gemini-4-hydrogen", "gemini-4-argon", "gemini-4-neon"]
    requires_result = False

    def list_models(self) -> dict[str, list[str]] | None:
        try:
            proc = subprocess.run([self.executable, "models"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return parse_models(proc.stdout, self.exclude) or None

    def probe(self) -> tuple[str | None, dict[str, list[str]] | None]:
        # `agy models` interroge le service : il n'aboutit que si le CLI est connecté.
        catalog = self.list_models()
        if catalog is None:
            return "non connecté ou `agy models` en échec (lancer `agy` pour se connecter)", None
        return None, catalog

    def resolve_model(self, model: str, effort: str | None, catalog: dict[str, list[str]] | None) -> str:
        family, fixed = split_model_id(model)
        if fixed:  # identifiant complet déjà fourni : on le respecte
            return model
        wanted = effort or DEFAULT_EFFORT
        if catalog is None:
            return f"{family}-{wanted}"
        efforts = catalog.get(family)
        if efforts is None:
            raise ValueError(f"modèle agy {family!r} absent de `agy models` (ou exclu)")
        if not efforts:  # famille sans variante d'effort (ex. orchestrateur)
            return family
        return f"{family}-{nearest_effort(wanted, efforts)}"

    def build_command(self, req: RunRequest) -> list[str]:
        cmd = [
            self.executable,
            "--print",
            req.prompt,
            "--model",
            req.model,
            "--output-format",
            "json",
            "--print-timeout",
            f"{req.timeout_sec}s",
        ]
        if req.permission_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd += ["--mode", "accept-edits"]
        cmd += req.extra_args
        return cmd

    def finalize(self, state: AttemptResult, lines: list[str], req: RunRequest) -> None:
        text = "".join(lines).strip()
        data = None
        # La sortie peut contenir du bruit avant/après l'objet JSON : on tente l'ensemble,
        # puis la dernière ligne qui ressemble à un objet.
        for candidate in [text] + [ln for ln in reversed(text.splitlines()) if ln.lstrip().startswith("{")]:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
        if data is None:
            state.result_text = text[-2000:]
            return
        state.final_json = data
        state.has_result = True
        usage = data.get("usage") or data.get("usageMetadata") or {}
        state.tokens_in = int(_first(usage, "input_tokens", "prompt_tokens", "promptTokenCount"))
        state.tokens_out = int(_first(usage, "output_tokens", "completion_tokens", "candidatesTokenCount"))
        state.cache_read = int(_first(usage, "cache_read_input_tokens", "cachedContentTokenCount"))
        state.cache_write = int(_first(usage, "cache_creation_input_tokens"))
        state.cost_usd = float(_first(data, "total_cost_usd", "cost_usd", default=0.0))
        state.turns = int(_first(data, "num_turns", "turns"))
        state.result_text = str(_first(data, "result", "response", "text", default=""))
        state.is_error = bool(data.get("is_error"))
