"""Adaptateur pour `codex exec` (OpenAI Codex CLI, flux `--json` : un événement JSON par ligne).

Vérifié sur `codex-cli 0.171.0` :
- dernier `agent_message` = résultat ; `turn.completed` = fin normale ; `turn.failed`/`error` = erreur ;
- `max_turns` est approché en comptant les actions de l'agent (commandes, modifications, outils MCP) ;
- `allowed_tools` et `max_budget_usd` ne sont pas transmis (pas d'équivalent) ; aucun coût n'est rapporté ;
- avec `permission != "bypass"`, `--sandbox workspace-write` monte `.git/` en lecture seule (bloque `git commit`).
La chaîne par défaut cible les modèles exposés avec un compte ChatGPT (`[escalation].codex` à adapter si clé API).
"""

from __future__ import annotations

import json
import os
import subprocess

from .base import Adapter, AttemptResult, RunRequest

EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
ACTION_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search"}


class CodexAdapter(Adapter):
    name = "codex"
    executable = "codex"
    default_chain = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-luna"]
    requires_result = True
    supports_allowed_tools = False
    supports_max_turns = True  # approché (actions d'agent comptées)
    supports_max_budget = False
    recommended_usage = "exécution standard OpenAI, sans restriction d'outils"
    baseline_model = "gpt-6-luna"
    model_metadata = {
        "gpt-5.6-luna": {
            "level": "N1",
            "context": "272k",
            "cost": "$",
            "pricing": (0.40, 0.40, 0.10, 1.60),
            "desc": "tâches mécaniques, format, doc, scripts simples",
        },
        "gpt-5.6-terra": {
            "level": "N2",
            "context": "272k",
            "cost": "$$",
            "pricing": (2.00, 2.00, 0.50, 8.00),
            "desc": "logique standard, nouvelles fonctions, refactor",
        },
        "gpt-6-luna": {
            "level": "N3",
            "context": "272k",
            "cost": "$$$",
            "pricing": (5.00, 5.00, 1.25, 20.00),
            "desc": "raisonnement avancé, architecture, diagnostic",
        },
    }

    def auth_problem(self) -> str | None:
        if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            return None
        try:
            proc = subprocess.run([self.executable, "login", "status"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None  # indéterminable : on ne bloque pas
        return None if proc.returncode == 0 else "non connecté (lancer `codex login`)"

    def build_command(self, req: RunRequest) -> list[str]:
        cmd = [self.executable, "exec", "--json", "--model", req.model]
        if req.permission_mode == "bypass":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd += ["--sandbox", "workspace-write"]
        if req.effort:
            cmd += ["-c", f"model_reasoning_effort={json.dumps(EFFORT_MAP[req.effort])}"]
        cmd += req.extra_args
        cmd += ["--", req.prompt]
        return cmd

    def on_line(self, line: str, state: AttemptResult, req: RunRequest) -> bool:
        line = line.strip()
        if not line.startswith("{"):
            return False
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False
        kind = event.get("type", "")
        item = event.get("item") or {}
        if kind == "item.completed":
            if item.get("type") == "agent_message":
                state.result_text = str(item.get("text") or "")
            elif item.get("type") in ACTION_ITEMS:
                state.turns += 1
                return state.turns > req.max_turns
        elif kind == "turn.completed":
            state.has_result = True
            usage = event.get("usage") or {}
            state.tokens_in += int(usage.get("input_tokens") or 0)
            state.cache_write += int(usage.get("cache_write_input_tokens") or 0)
            state.cache_read += int(usage.get("cached_input_tokens") or 0)
            state.tokens_out += int(usage.get("output_tokens") or 0)
            state.final_json = event
        elif kind in ("turn.failed", "error"):
            err = event.get("error") if isinstance(event.get("error"), dict) else event
            state.is_error = True
            state.result_text = str(err.get("message") or line)
            state.final_json = event
        return False
