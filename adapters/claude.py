"""Adaptateur pour `claude -p` (flux `stream-json`, un événement JSON par ligne)."""

from __future__ import annotations

import json
import os
import subprocess

from .base import Adapter, AttemptResult, RunRequest


class ClaudeAdapter(Adapter):
    name = "claude"
    executable = "claude"
    default_chain = ["haiku", "sonnet", "opus", "fable"]
    requires_result = True

    def __init__(self, exclude: list[str] | None = None) -> None:
        super().__init__(exclude)
        self._seen: set[str] = set()

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
            msg_id = (event.get("message") or {}).get("id") or f"anon-{state.turns}"
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
