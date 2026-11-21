"""Adaptateur pour `agy` (mode `--print`, sortie `--output-format json`).

`agy` n'expose ni liste blanche d'outils, ni plafond de tours, ni budget : `allowed_tools`,
`max_turns` (non vérifiable) et `max_budget_usd` ne sont donc pas transmis. Le timeout est
appliqué par le runner (et relayé via `--print-timeout`). Le format JSON exact n'étant pas
documenté dans `agy --help`, l'analyse des métriques est tolérante : à défaut, les compteurs
restent à 0 et seuls le code retour et `is_error` décident du succès (C1).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from fnmatch import fnmatch

from .base import Adapter, AttemptResult, RunRequest, WindowQuota

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
_SUFFIX = re.compile(r"^(?P<family>.+)-(?P<effort>" + "|".join(EFFORT_LEVELS) + r")$")
DEFAULT_EFFORT = "medium"
_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._]*-[a-z0-9._-]+")
HEADLESS_DENIED_RE = re.compile(r"headless mode cannot prompt for, so it was auto-denied", re.IGNORECASE)


def _parse_agy_delay(reset_str: str | None) -> tuple[str, float | None]:
    if not reset_str:
        return "", None
    try:
        clean = reset_str.strip().replace("Z", "+00:00")
        if "T" in clean:
            target = datetime.fromisoformat(clean)
            diff = (target - datetime.now(timezone.utc)).total_seconds()
        else:
            dt_part = clean.rsplit(" ", 1)[0]
            target = datetime.strptime(dt_part, "%Y-%m-%d %H:%M")
            diff = (target - datetime.now()).total_seconds()

        mins = max(0.0, diff / 60.0)
        if diff <= 0:
            return "imminent", 0.0
        if diff < 3600:
            return f"{int(diff // 60)}m", mins
        if diff < 86400:
            h = int(diff // 3600)
            m = int((diff % 3600) // 60)
            return f"{h}h{m:02d}m", mins
        days = int(diff // 86400)
        h = int((diff % 86400) // 3600)
        return f"{days}j {h}h", mins
    except Exception:
        return "", None


def parse_agy_usage(text: str) -> list[WindowQuota]:
    """Parse la sortie de `agy --print /usage` (séparation par tabulations ou espaces)."""
    windows: dict[str, WindowQuota] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("quota:"):
            continue

        # Essai par tabulation
        tokens = [t.strip() for t in line.split("\t") if t.strip()]
        # Repli si colonnes séparées par 2+ espaces
        if len(tokens) < 4:
            parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]
            if len(parts) >= 4:
                tokens = parts

        if len(tokens) < 4:
            continue

        grp, lim, pct_str, reset_str = tokens[0].lower(), tokens[1].lower(), tokens[2].rstrip("%"), tokens[3]
        try:
            rem_pct = float(pct_str)
        except ValueError:
            continue
        used_pct = max(0.0, min(100.0, 100.0 - rem_pct))
        delay, mins = _parse_agy_delay(reset_str)

        if "gemini models" in grp:
            if "five hour" in lim:
                windows["session"] = WindowQuota("session (5h)", used_pct, reset_str, delay, mins)
            elif "weekly" in lim:
                windows["weekly"] = WindowQuota("semaine (7j)", used_pct, reset_str, delay, mins)
        elif "gemini 4 argon" in grp and "weekly" in lim:
            windows["argon"] = WindowQuota("argon (7j)", used_pct, reset_str, delay, mins)

    ordered: list[WindowQuota] = []
    for k in ("session", "weekly", "argon"):
        if k in windows:
            ordered.append(windows[k])
    return ordered


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


def _extract_json(text: str) -> dict | None:
    """Objet JSON final d'une sortie pouvant contenir du bruit (infos, avertissements) avant/après.

    Essaie d'abord le texte entier, puis décode un objet à partir de chaque ligne commençant par `{`
    (le JSON peut être indenté sur plusieurs lignes). Les objets imbriqués dans un objet déjà décodé
    sont ignorés ; on retient le dernier objet de premier niveau.
    """
    try:
        whole = json.loads(text)
        if isinstance(whole, dict):
            return whole
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    found: dict | None = None
    covered_until = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        start = offset + len(line) - len(line.lstrip())
        offset += len(line)
        if start < covered_until or not line.lstrip().startswith("{"):
            continue
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found, covered_until = obj, end
    return found


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
    supports_allowed_tools = False
    supports_max_turns = False
    supports_max_budget = False
    recommended_usage = "lecture transverse multi-fichiers, très large contexte (1M+), audits, économie de quota"
    baseline_model = "gemini-4-neon"
    model_metadata = {
        "gemini-3.8-flash": {
            "level": "N1",
            "context": "1M+",
            "cost": "$",
            "pricing": (0.15, 0.15, 0.0375, 0.60),
            "desc": "rapide, économique, grand contexte, doc/audits, mécanique",
        },
        "gemini-4-hydrogen": {
            "level": "N2",
            "context": "1M+",
            "cost": "$",
            "pricing": (0.50, 0.50, 0.125, 2.00),
            "desc": "rapide, logique locale simple à modérée",
        },
        "gemini-4-argon": {
            "level": "N2+",
            "context": "1M+",
            "cost": "$$",
            "pricing": (1.25, 1.25, 0.3125, 10.00),
            "quota_group": "argon",
            "premium": True,
            "desc": "logique avancée, refactor transverse multi-fichiers",
        },
        "gemini-4-neon": {
            "level": "N3",
            "context": "1M+",
            "cost": "$$$",
            "pricing": (2.50, 2.50, 0.625, 15.00),
            "premium": True,
            "desc": "raisonnement frontière, diagnostic, invariants complexes",
        },
    }

    def get_model_info(self, model: str) -> dict[str, str] | None:
        family, _ = split_model_id(model)
        return self.model_metadata.get(family) or self.model_metadata.get(model)

    def get_usage_windows(self) -> list[WindowQuota] | None:
        mock = os.environ.get("FAKE_AGY_USAGE")
        if mock:
            try:
                data = json.loads(mock)
                return [WindowQuota(**item) for item in data]
            except Exception:
                pass
        try:
            proc = subprocess.run(
                [self.executable, "--print", "/usage"],
                capture_output=True, text=True, timeout=12
            )
            if proc.returncode == 0 and proc.stdout:
                parsed = parse_agy_usage(proc.stdout)
                if parsed:
                    return parsed
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

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
        self._seen_steps: set[int] = set()
        self._step_usage: dict[int, dict] = {}
        cmd = [
            self.executable,
            "--print",
            req.prompt,
            "--model",
            req.model,
            "--output-format",
            "stream-json",
            "--print-timeout",
            f"{req.timeout_sec}s",
        ]
        if req.permission_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd += ["--mode", "accept-edits"]
        cmd += req.extra_args
        return cmd

    def on_line(self, line: str, state: AttemptResult, req: RunRequest) -> bool:
        line = line.strip()
        if not line.startswith("{"):
            return False
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False
        kind = event.get("event")
        if kind == "step_update":
            su = event.get("step_update") or {}
            if su.get("step_type") == "agent_response" and su.get("state") == "DONE":
                idx = su.get("step_index", state.turns)
                if idx not in self._seen_steps:
                    self._seen_steps.add(idx)
                    state.turns += 1
                if isinstance(su.get("usage"), dict):
                    self._step_usage[idx] = su["usage"]
        elif kind == "result":
            res = event.get("result")
            state.final_json = res if isinstance(res, dict) else event
        return False

    def finalize(self, state: AttemptResult, lines: list[str], req: RunRequest) -> None:
        text = "".join(lines).strip()
        if req.permission_mode != "bypass":
            for raw in lines:
                s = raw.strip()
                if not s.startswith("{") and HEADLESS_DENIED_RE.search(s):
                    state.permission_denied = True
                    break
        data = state.final_json if state.final_json is not None else _extract_json(text)
        if data is None:
            if getattr(self, "_step_usage", None):
                state.tokens_in = sum(int(_first(u, "input_tokens", "prompt_tokens", "promptTokenCount")) for u in self._step_usage.values())
                state.tokens_out = sum(int(_first(u, "output_tokens", "completion_tokens", "candidatesTokenCount")) for u in self._step_usage.values())
                state.cache_read = sum(int(_first(u, "cache_read_input_tokens", "cache_read_tokens", "cachedContentTokenCount")) for u in self._step_usage.values())
            state.result_text = text[-2000:]
            return
        if req.permission_mode != "bypass" and isinstance(data.get("error"), str) and HEADLESS_DENIED_RE.search(data["error"]):
            state.permission_denied = True
        state.final_json = data
        state.has_result = True
        usage = data.get("usage") or data.get("usageMetadata") or {}
        state.tokens_in = int(_first(usage, "input_tokens", "prompt_tokens", "promptTokenCount"))
        state.tokens_out = int(_first(usage, "output_tokens", "completion_tokens", "candidatesTokenCount"))
        state.cache_read = int(_first(usage, "cache_read_input_tokens", "cache_read_tokens", "cachedContentTokenCount"))
        state.cache_write = int(_first(usage, "cache_creation_input_tokens"))
        state.cost_usd = float(_first(data, "total_cost_usd", "cost_usd", default=0.0))
        state.turns = max(state.turns, int(_first(data, "num_turns", "turns")))
        state.result_text = str(_first(data, "result", "response", "text", default=""))
        state.is_error = bool(data.get("is_error") or str(data.get("status", "")).upper() in ("ERROR", "FAILED"))
