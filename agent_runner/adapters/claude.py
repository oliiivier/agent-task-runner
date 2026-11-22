"""Adaptateur pour `claude -p` (flux `stream-json`, un événement JSON par ligne)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .base import Adapter, AttemptResult, RunRequest, WindowQuota


def format_reset_delay(resets_at: str | None) -> tuple[str, float | None]:
    """Calcule (délai lisible, minutes restantes) avant le reset."""
    if not resets_at:
        return "", None
    try:
        clean = resets_at.replace("Z", "+00:00")
        target = datetime.fromisoformat(clean)
        now = datetime.now(timezone.utc)
        diff = (target - now).total_seconds()
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


def get_claude_oauth_token() -> str | None:
    """Récupère le jeton OAuth de session Claude (env, credentials.json ou Keychain macOS)."""
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        if env_token.strip().startswith("{"):
            try:
                data = json.loads(env_token)
                return data.get("claudeAiOauth", {}).get("accessToken") or data.get("accessToken")
            except json.JSONDecodeError:
                pass
        return env_token.strip()

    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.is_file():
        try:
            data = json.loads(creds_path.read_text(encoding="utf-8"))
            token = data.get("claudeAiOauth", {}).get("accessToken")
            if token:
                return token
        except (OSError, json.JSONDecodeError):
            pass

    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0 and proc.stdout.strip():
                txt = proc.stdout.strip()
                if txt.startswith("{"):
                    data = json.loads(txt)
                    return data.get("claudeAiOauth", {}).get("accessToken")
                return txt
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

    return None


class ClaudeAdapter(Adapter):
    name = "claude"
    executable = "claude"
    default_chain = ["haiku", "sonnet", "opus", "fable"]
    requires_result = True
    supports_allowed_tools = True
    supports_max_turns = True
    supports_max_budget = True
    recommended_usage = "édition chirurgicale, outillage restreint, boucles courtes compilation/test"
    baseline_model = "opus"
    model_metadata = {
        "haiku": {
            "level": "N1",
            "context": "200k",
            "cost": "$",
            "pricing": (0.80, 1.00, 0.08, 4.00),
            "desc": "tâches mécaniques, déplacements, format, doc, tests ciblés",
        },
        "sonnet": {
            "level": "N2",
            "context": "200k",
            "cost": "$$",
            "pricing": (3.00, 3.75, 0.30, 15.00),
            "desc": "logique locale, 1 à 3 fichiers, nouvelles fonctions, refactor",
        },
        "opus": {
            "level": "N3",
            "context": "200k",
            "cost": "$$$",
            "pricing": (15.00, 18.75, 1.50, 75.00),
            "premium": True,
            "desc": "architecture, invariants complexes, concurrence, code sensible",
        },
        "fable": {
            "level": "N3+",
            "context": "200k",
            "cost": "$$$$",
            "pricing": (30.00, 37.50, 3.00, 150.00),
            "premium": True,
            "desc": "plafond d'escalade ultime, haute ambiguïté",
        },
    }

    def __init__(self, exclude: list[str] | None = None) -> None:
        super().__init__(exclude)
        self._seen: set[str] = set()
        self._usage: dict[str, dict] = {}  # usage cumulé par message (repli si aucun `result`)

    def get_usage_windows(self) -> list[WindowQuota] | None:
        """Interroge l'endpoint OAuth d'Anthropic pour obtenir les fenêtres 5h et 7j."""
        self.last_usage_error = None
        mock = os.environ.get("FAKE_CLAUDE_USAGE")
        if mock is not None:
            if not mock:
                return None
            try:
                data = json.loads(mock)
                return [WindowQuota(**item) for item in data]
            except Exception:
                return None
        token = get_claude_oauth_token()
        if not token:
            self.last_usage_error = "nécessite un compte OAuth connecté"
            return None
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/0.2.29",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_usage_error = f"erreur HTTP {exc.code} sur /api/oauth/usage"
            return None
        except TimeoutError:
            self.last_usage_error = "timeout réseau sur /api/oauth/usage"
            return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            self.last_usage_error = f"échec requête /api/oauth/usage ({type(exc).__name__})"
            return None

        windows: list[WindowQuota] = []
        five = data.get("five_hour")
        if isinstance(five, dict) and "utilization" in five:
            delay, mins = format_reset_delay(five.get("resets_at"))
            windows.append(WindowQuota("session (5h)", float(five["utilization"]), five.get("resets_at"), delay, mins))

        seven = data.get("seven_day")
        if isinstance(seven, dict) and "utilization" in seven:
            delay, mins = format_reset_delay(seven.get("resets_at"))
            windows.append(WindowQuota("semaine (7j)", float(seven["utilization"]), seven.get("resets_at"), delay, mins))

        for key, label in [("seven_day_sonnet", "sonnet (7j)"), ("seven_day_opus", "opus (7j)")]:
            sub = data.get(key)
            if isinstance(sub, dict) and "utilization" in sub:
                delay, mins = format_reset_delay(sub.get("resets_at"))
                windows.append(WindowQuota(label, float(sub["utilization"]), sub.get("resets_at"), delay, mins))

        return windows or None

    def auth_problem(self) -> str | None:
        # Une clé/jeton fourni par l'environnement suffit : pas de session à vérifier.
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return None
        try:
            proc = subprocess.run([self.executable, "auth", "status"], capture_output=True, text=True, timeout=30)
            status = json.loads(proc.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None  # CLI ancien ou sortie inattendue : indéterminable, on ne bloque pas
        if isinstance(status, dict) and status.get("loggedIn") is False:
            return "non connecté (lancer `claude auth login`)"
        return None

    def build_command(self, req: RunRequest) -> list[str]:
        self._seen = set()
        self._usage = {}
        cmd = [
            self.executable,
            "-p",
            req.prompt,
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            req.model,
        ]
        if req.permission_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd += ["--permission-mode", "acceptEdits"]
        if req.effort:
            cmd += ["--effort", req.effort]
        if req.allowed_tools:
            cmd += ["--allowedTools", ",".join(req.allowed_tools)]
        if req.max_budget_usd is not None:
            cmd += ["--max-budget-usd", str(req.max_budget_usd)]
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
        kind = event.get("type")
        if kind == "assistant":
            message = event.get("message") or {}
            msg_id = message.get("id") or f"anon-{state.turns}"
            if isinstance(message.get("usage"), dict):
                # Un même message revient par bloc de contenu : on garde la valeur maximale par champ.
                acc = self._usage.setdefault(msg_id, {})
                for key, val in message["usage"].items():
                    if isinstance(val, int) and not isinstance(val, bool):
                        acc[key] = max(acc.get(key, 0), val)
            if msg_id not in self._seen:
                self._seen.add(msg_id)
                state.turns += 1
                # Le plafond est dépassé dès qu'un tour de plus que max_turns démarre.
                return state.turns > req.max_turns
        elif kind == "result":
            state.final_json = event
        return False

    def finalize(self, state: AttemptResult, lines: list[str], req: RunRequest) -> None:
        res = state.final_json
        if not res:
            # Pas d'événement `result` (timeout, max_turns, SIGKILL) : on récupère au moins les tokens
            # consommés d'après les événements `assistant` ; le coût en dollars reste inconnu (0).
            def total(key: str) -> int:
                return sum(u.get(key, 0) for u in self._usage.values())
            state.tokens_in = total("input_tokens")
            state.cache_write = total("cache_creation_input_tokens")
            state.cache_read = total("cache_read_input_tokens")
            state.tokens_out = total("output_tokens")
            return
        state.has_result = True
        usage = res.get("usage") or {}
        state.tokens_in = int(usage.get("input_tokens") or 0)
        state.cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        state.cache_read = int(usage.get("cache_read_input_tokens") or 0)
        state.tokens_out = int(usage.get("output_tokens") or 0)
        state.cost_usd = float(res.get("total_cost_usd") or 0.0)
        state.turns = int(res.get("num_turns") or state.turns)
        state.result_text = str(res.get("result") or "")
        state.is_error = bool(res.get("is_error"))
        if str(res.get("subtype", "")).startswith("error_max_turns"):
            state.max_turns_hit = True
