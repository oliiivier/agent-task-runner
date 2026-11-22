"""Formatage lisible des événements `stream-json` (Style, Claude, Codex)."""

from __future__ import annotations

import json

class Style:
    """Applique des styles ANSI conditionnels (terminal, pager ou --no-color)."""

    def __init__(self, color: bool) -> None:
        self.color = color

    def wrap(self, code: str, text: str) -> str:
        if not self.color or not text:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self.wrap("1", text)

    def dim(self, text: str) -> str:
        return self.wrap("2", text)

    def red(self, text: str) -> str:
        return self.wrap("1;31", text)

    def green(self, text: str) -> str:
        return self.wrap("32", text)

    def yellow(self, text: str) -> str:
        return self.wrap("33", text)

    def cyan(self, text: str) -> str:
        return self.wrap("36", text)

    def magenta(self, text: str) -> str:
        return self.wrap("35", text)


def _truncate_text(text: str, max_lines: int = 8, max_chars: int = 400) -> list[str]:
    """Condense une sortie longue (tool_result, stdout) en quelques lignes lisibles."""
    text = text.rstrip("\n")
    if not text:
        return []
    lines = text.splitlines()
    clipped = False
    if len(lines) > max_lines:
        extra_lines = len(lines) - max_lines
        lines = lines[:max_lines]
        clipped = True
    else:
        extra_lines = 0
    out: list[str] = []
    total = 0
    for ln in lines:
        if total + len(ln) > max_chars:
            rem = max(0, max_chars - total)
            out.append(ln[:rem] + "…")
            clipped = True
            break
        out.append(ln)
        total += len(ln)
    if clipped:
        suffix = f"... (+{extra_lines} ligne(s))" if extra_lines > 0 else "... (tronqué)"
        out.append(suffix)
    return out


def _format_tool_args(name: str, inp: object) -> str:
    """Extrait l'argument principal d'un appel d'outil pour un résumé compact."""
    if not isinstance(inp, dict):
        s = str(inp) if inp else ""
        return (s[:100] + "…") if len(s) > 100 else s
    for key in ("file_path", "path", "command", "cmd", "pattern", "query", "url", "description"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            one_line = " ".join(val.strip().splitlines())
            return (one_line[:120] + "…") if len(one_line) > 120 else one_line
    if inp:
        compact = json.dumps(inp, ensure_ascii=False)
        return (compact[:120] + "…") if len(compact) > 120 else compact
    return ""


def _extract_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(content) if content is not None else ""


def _format_claude_event(event: dict, style: Style) -> list[str] | None:
    kind = event.get("type")
    if kind == "system":
        sub = event.get("subtype") or "system"
        model = event.get("model")
        info = f"model={model}" if model else ""
        return [style.dim(f"[{sub}] {info}".strip())]

    if kind == "assistant":
        msg = event.get("message") or {}
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            return []
        out: list[str] = []
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            btype = blk.get("type")
            if btype == "thinking":
                thinking = str(blk.get("thinking") or "").strip()
                if thinking:
                    preview = _truncate_text(thinking, max_lines=3, max_chars=240)
                    out.append(style.dim("[thinking] " + " / ".join(preview)))
            elif btype == "text":
                text = str(blk.get("text") or "").strip()
                if text:
                    for ln in text.splitlines():
                        out.append(ln)
            elif btype == "tool_use":
                name = str(blk.get("name") or "tool")
                arg = _format_tool_args(name, blk.get("input"))
                label = style.yellow(style.bold(f"[tool:{name}]"))
                out.append(f"{label} {arg}" if arg else label)
        return out

    if kind == "user":
        msg = event.get("message") or {}
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            return []
        out = []
        for blk in blocks:
            if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                continue
            is_err = bool(blk.get("is_error"))
            txt = _extract_content_text(blk.get("content"))
            lines = _truncate_text(txt, max_lines=6, max_chars=360)
            prefix = style.red("[result:error]") if is_err else style.green("[result]")
            if not lines:
                out.append(prefix + " " + style.dim("(vide)"))
            else:
                out.append(f"{prefix} {style.red(lines[0]) if is_err else style.dim(lines[0])}")
                for ln in lines[1:]:
                    out.append("  " + (style.red(ln) if is_err else style.dim(ln)))
        return out

    if kind == "result":
        is_err = bool(event.get("is_error"))
        turns = event.get("num_turns")
        cost = event.get("total_cost_usd")
        dur_ms = event.get("duration_ms")
        meta: list[str] = []
        if turns is not None:
            meta.append(f"tours={turns}")
        if isinstance(cost, (int, float)):
            meta.append(f"coût=${cost:.4f}")
        if isinstance(dur_ms, (int, float)):
            meta.append(f"durée={dur_ms / 1000:.1f}s")
        tag = style.red("[erreur]") if is_err else style.green(style.bold("[terminé]"))
        header = f"{tag} " + style.dim(" ".join(meta))
        res_txt = str(event.get("result") or "").strip()
        out = [header.strip()]
        if res_txt:
            for ln in _truncate_text(res_txt, max_lines=60, max_chars=4000):
                out.append("  " + (style.red(ln) if is_err else ln))
        return out

    return None


def _format_codex_event(event: dict, style: Style) -> list[str] | None:
    kind = str(event.get("type") or "")
    if kind == "thread.started":
        tid = event.get("thread_id") or ""
        return [style.dim(f"[thread] {tid}".strip())]
    if kind == "turn.started":
        return [style.dim("[turn] démarré")]
    if kind == "item.completed":
        item = event.get("item") or {}
        itype = str(item.get("type") or "")
        if itype == "agent_message":
            txt = str(item.get("text") or "").strip()
            return txt.splitlines() if txt else []
        if itype == "reasoning":
            txt = str(item.get("text") or "").strip()
            if txt:
                return [style.dim("[thinking] " + " / ".join(_truncate_text(txt, max_lines=3, max_chars=240)))]
            return []
        if itype == "command_execution":
            cmd = str(item.get("command") or "")
            code = item.get("exit_code")
            label = style.yellow(style.bold("[cmd]"))
            suffix = style.dim(f" (exit={code})") if code is not None else ""
            out = [f"{label} {cmd}{suffix}"]
            agg = str(item.get("aggregated_output") or "").strip()
            if agg:
                for ln in _truncate_text(agg, max_lines=6, max_chars=360):
                    out.append("  " + (style.red(ln) if code and code != 0 else style.dim(ln)))
            return out
        if itype == "file_change":
            changes = item.get("changes") or []
            if isinstance(changes, list) and changes:
                return [style.cyan("[file] ") + ", ".join(str(c.get("path") or c) for c in changes[:5])]
            return [style.cyan(f"[{itype}]")]
        return [style.dim(f"[{itype}]") if itype else ""]

    if kind == "turn.completed":
        usage = event.get("usage") or {}
        tin = usage.get("input_tokens", 0)
        tout = usage.get("output_tokens", 0)
        return [style.green(style.bold("[turn terminé]")) + " " + style.dim(f"in={tin} out={tout}")]

    if kind in ("turn.failed", "error"):
        err = event.get("error") if isinstance(event.get("error"), dict) else event
        msg = str(err.get("message") or event.get("message") or json.dumps(event, ensure_ascii=False))
        return [style.red(f"[erreur] {msg}")]

    return None
