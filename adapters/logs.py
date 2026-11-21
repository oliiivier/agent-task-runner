"""Rendu lisible et suivi temps réel des journaux `stream-json` d'agent-runner."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


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


_AGY_META_KEYS = {
    "conversation_id", "step_index", "state", "step_type",
    "timestamp", "created_at", "updated_at", "usage", "usageMetadata",
    "duration_seconds", "tool_name",
}


def _agy_pick_str(containers: list[dict], *keys: str) -> str:
    lowered = tuple(k.lower() for k in keys)
    for c in containers:
        for k in keys:
            v = c.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # Supporte les clés PascalCase (Path, CommandLine, TargetFile, etc.) dans tool_info.parameters
        for ck, cv in c.items():
            if ck.lower() in lowered and isinstance(cv, str) and cv.strip():
                return cv.strip()
    return ""


def _agy_containers(su: dict, stype: str) -> list[dict]:
    """Retourne `su` et ses sous-dicts pertinents (ex. `tool_info`, `tool_info.parameters`)."""
    containers = [su]
    for k in (stype, "tool_info", "tool", "tool_call", "call", "data", "payload", "metadata", "input", "arguments", "params"):
        v = su.get(k)
        if isinstance(v, dict):
            containers.append(v)
            for subk in ("parameters", "params", "arguments", "input"):
                sv = v.get(subk)
                if isinstance(sv, dict):
                    containers.append(sv)
    return containers


def _agy_remaining_summary(su: dict) -> str:
    extra = {k: v for k, v in su.items() if k not in _AGY_META_KEYS and v not in (None, "", [], {})}
    if not extra:
        return ""
    compact = json.dumps(extra, ensure_ascii=False)
    return (compact[:200] + "…") if len(compact) > 200 else compact


def _format_agy_event(event: dict, style: Style) -> list[str] | None:
    kind = event.get("event")
    if kind == "init":
        info = event.get("init") if isinstance(event.get("init"), dict) else event
        model = info.get("model") or ""
        cwd = info.get("cwd") or ""
        parts = [f"model={model}" if model else "", f"cwd={cwd}" if cwd else ""]
        summary = " ".join(p for p in parts if p) or _agy_remaining_summary(info)
        return [style.dim(f"[init] {summary}".strip())]

    if kind == "step_update":
        su = event.get("step_update") or {}
        if not isinstance(su, dict):
            return []
        stype = str(su.get("step_type") or "step")
        state = str(su.get("state") or "").upper()
        # Les mises à jour intermédiaires (ACTIVE, PENDING, etc.) répètent le même step : on les filtre.
        if state in ("PENDING", "ACTIVE", "GENERATING", "RUNNING", "WAITING"):
            return []

        is_err = state in ("ERROR", "FAILED", "CANCELED")
        containers = _agy_containers(su, stype)
        out: list[str] = []

        thinking = _agy_pick_str(containers, "thinking", "thought", "reasoning")
        if thinking:
            preview = _truncate_text(thinking, max_lines=3, max_chars=240)
            out.append(style.dim("[thinking] " + " / ".join(preview)))

        tool_calls = su.get("tool_calls") or (containers[1].get("tool_calls") if len(containers) > 1 else None)
        if isinstance(tool_calls, list) and tool_calls:
            for tool in tool_calls:
                if isinstance(tool, dict):
                    tname = str(tool.get("name") or tool.get("tool_name") or "tool")
                    targ = _format_tool_args(tname, tool.get("arguments") or tool.get("input"))
                    out.append(f"{style.yellow(style.bold(f'[tool:{tname}]'))} {targ}".strip())

        text = _agy_pick_str(containers, "text", "response", "content", "message", "summary", "text_delta")
        dur = su.get("duration_seconds")
        dur_str = f"{dur:.1f}s" if isinstance(dur, (int, float)) else ""

        if stype == "user_input":
            if text:
                first = " ".join(text.splitlines())
                if len(first) > 120:
                    first = first[:120] + "…"
                out.append(style.dim(f"[prompt] {first}"))
            return out

        if stype in ("agent_response", "planner_response", "response"):
            idx = su.get("step_index")
            tag = f"[step {idx}]" if idx is not None else "[step]"
            usage = su.get("usage") or su.get("usageMetadata")
            meta_parts: list[str] = []
            if dur_str:
                meta_parts.append(dur_str)
            if isinstance(usage, dict):
                tin = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("promptTokenCount")
                tout = usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("candidatesTokenCount")
                if tin is not None or tout is not None:
                    meta_parts.append(f"in={tin or 0} out={tout or 0}")
            meta_suffix = f" ({', '.join(meta_parts)})" if meta_parts else ""
            if text:
                if out:
                    out.append(style.dim(f"{tag}{meta_suffix}"))
                out.extend(text.splitlines())
            elif not out:
                out.append(style.dim(f"{tag} {stype}{meta_suffix}"))
            return out

        if stype == "system_message":
            msg = text or _agy_remaining_summary(su)
            if msg:
                for ln in _truncate_text(msg, max_lines=6, max_chars=360):
                    out.append(style.dim(f"[system] {ln}"))
            return out

        # Étape d'outil (`tool` ou step_type nommé)
        tname = _agy_pick_str(containers, "tool_name", "toolName", "name", "function_name", "action")
        if not tname and isinstance(su.get("tool"), str):
            tname = su["tool"].strip()
        label_name = f"tool:{tname}" if (tname and stype == "tool") else (tname or stype)
        arg = _agy_pick_str(
            containers,
            "command_line", "commandline", "command", "cmd",
            "target_file", "targetfile", "absolute_path", "absolutepath",
            "file_path", "filepath", "path",
            "search_path", "searchpath", "directory_path", "directorypath",
            "query", "pattern", "url", "description",
        )
        if not out:
            label = style.red(f"[{label_name}:{state}]") if is_err else style.yellow(style.bold(f"[{label_name}]"))
            dur_tag = (" " + style.dim(f"({dur_str})")) if dur_str else ""
            if arg:
                one = " ".join(arg.splitlines())
                out.append(f"{label} {(one[:140] + '…') if len(one) > 140 else one}{dur_tag}")
            elif not text:
                extra = _agy_remaining_summary(su)
                out.append(f"{label}{dur_tag} {style.dim(extra)}".strip())
            else:
                out.append(f"{label}{dur_tag}")

        output_txt = _agy_pick_str(containers, "output", "stdout", "result")
        body = text or output_txt
        if body:
            for ln in _truncate_text(body, max_lines=6, max_chars=360):
                out.append("  " + (style.red(ln) if is_err else style.dim(ln)))
        err_msg = _agy_pick_str(containers, "error", "stderr")
        if err_msg:
            for ln in _truncate_text(err_msg, max_lines=4, max_chars=300):
                out.append("  " + style.red(ln))
        return out

    if kind == "result":
        res = event.get("result")
        data = res if isinstance(res, dict) else event
        is_err = bool(data.get("is_error") or str(data.get("status", "")).upper() in ("ERROR", "FAILED"))
        usage = data.get("usage") or data.get("usageMetadata") or {}
        meta: list[str] = []
        turns = data.get("num_turns") or data.get("turns")
        if turns is not None:
            meta.append(f"tours={turns}")
        if isinstance(usage, dict) and usage:
            tin = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("promptTokenCount")
            tout = usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("candidatesTokenCount")
            if tin is not None or tout is not None:
                meta.append(f"tokens={tin or 0}->{tout or 0}")
        cost = data.get("total_cost_usd") or data.get("cost_usd")
        if isinstance(cost, (int, float)) and cost > 0:
            meta.append(f"coût=${cost:.4f}")
        tag = style.red("[erreur]") if is_err else style.green(style.bold("[terminé]"))
        header = f"{tag} " + style.dim(" ".join(meta))
        txt = str(data.get("result") or data.get("response") or data.get("text") or "").strip()
        out = [header.strip()]
        if txt:
            for ln in _truncate_text(txt, max_lines=60, max_chars=4000):
                out.append("  " + (style.red(ln) if is_err else ln))
        return out

    if kind in ("heartbeat", "ping"):
        return []

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


def format_log_line(line: str, style: Style, raw: bool = False) -> list[str]:
    """Transforme une ligne brute (JSONL stream-json ou texte) en lignes lisibles."""
    stripped = line.rstrip("\r\n")
    if raw:
        return [stripped]
    s = stripped.strip()
    if not s:
        return []
    if not s.startswith("{"):
        lower = s.lower()
        if any(tok in lower for tok in ("traceback", "error:", "failed", "exception")):
            return [style.red(stripped)]
        return [stripped]
    try:
        event = json.loads(s)
    except json.JSONDecodeError:
        return [stripped]
    if not isinstance(event, dict):
        return [stripped]

    if "event" in event:
        lines = _format_agy_event(event, style)
        if lines is not None:
            return lines
    if "conversation_id" in event and ("response" in event or "status" in event):
        lines = _format_agy_event({"event": "result", "result": event}, style)
        if lines is not None:
            return lines
    kind = str(event.get("type") or "")
    if kind in ("system", "assistant", "user", "result"):
        lines = _format_claude_event(event, style)
        if lines is not None:
            return lines
    if kind in ("thread.started", "turn.started", "item.completed", "turn.completed", "turn.failed", "error"):
        lines = _format_codex_event(event, style)
        if lines is not None:
            return lines

    # Événement JSON inconnu : résumé compact plutôt que perte silencieuse
    tag = event.get("type") or event.get("event") or "json"
    compact = json.dumps(event, ensure_ascii=False)
    if len(compact) > 140:
        compact = compact[:140] + "…"
    return [style.dim(f"[{tag}] {compact}")]


def verify_log_for(log_path: Path) -> Path | None:
    """Retourne le `.verify.log` associé à un `.log` d'agent s'il existe."""
    if log_path.name.endswith(".verify.log"):
        return None
    if log_path.suffix == ".log":
        cand = log_path.with_suffix(".verify.log")
        if cand.is_file():
            return cand
    return None


def _is_agent_log(path: Path) -> bool:
    return path.is_file() and path.name.endswith(".log") and not path.name.endswith(".verify.log")


def _sort_logs_chronological(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: (p.stat().st_mtime, p.name))


def collect_batch_task_ids(base_dir: Path, batch: str, parse_frontmatter_fn) -> set[str]:
    """Retrouve les identifiants de tâches d'un lot via usage.tsv et les fiches todo/done/failed."""
    ids: set[str] = set()
    usage = base_dir / "logs" / "usage.tsv"
    if usage.is_file():
        for ln in usage.read_text(errors="replace").splitlines()[1:]:
            parts = ln.split("\t")
            if len(parts) >= 3 and parts[1] == batch:
                ids.add(parts[2])
    for folder in ("todo", "done", "failed"):
        d = base_dir / folder
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            try:
                meta, _ = parse_frontmatter_fn(p.read_text(errors="replace"))
            except ValueError:
                continue
            if str(meta.get("batch") or "default") == batch:
                ids.add(str(meta.get("id") or p.stem))
    return ids


def find_matching_logs(
    logs_dir: Path,
    *,
    target: str | None = None,
    batch_ids: set[str] | None = None,
) -> list[Path]:
    """Liste les fichiers `.log` correspondant aux filtres, triés chronologiquement."""
    if not logs_dir.is_dir():
        return []
    candidates = [p for p in logs_dir.iterdir() if _is_agent_log(p)]
    if batch_ids is not None:
        candidates = [p for p in candidates if any(p.name.startswith(f"{tid}.try") for tid in batch_ids)]
    if target is not None:
        candidates = [
            p for p in candidates
            if p.name.startswith(f"{target}.try") or p.stem == target or p.name == target
        ]
    return _sort_logs_chronological(candidates)


def _format_file_header(path: Path, style: Style) -> str:
    return style.cyan(style.bold(f"=== {path.name} ==="))


def _format_verify_section(vpath: Path, style: Style, raw: bool) -> list[str]:
    out = [style.magenta(style.bold(f"--- {vpath.name} ---"))]
    for ln in vpath.read_text(errors="replace").splitlines():
        out.extend(format_log_line(ln, style, raw=raw))
    return out


def _open_pager() -> subprocess.Popen | None:
    pager_env = os.environ.get("PAGER")
    if pager_env:
        cmd = shlex.split(pager_env)
    else:
        less = shutil.which("less")
        if not less:
            return None
        cmd = [less, "-R", "-F", "-X"]
    try:
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
    except OSError:
        return None


def render_logs_cold(
    files: list[Path],
    style: Style,
    *,
    raw: bool = False,
    include_verify: bool = False,
    use_pager: bool = False,
    out=None,
) -> None:
    """Affiche un ou plusieurs fichiers de logs complets (avec pager optionnel)."""
    out = out or sys.stdout
    pager_proc = _open_pager() if use_pager else None
    dest = pager_proc.stdin if (pager_proc and pager_proc.stdin) else out
    try:
        for idx, path in enumerate(files):
            if idx > 0:
                dest.write("\n")
            dest.write(_format_file_header(path, style) + "\n")
            with path.open("r", errors="replace") as fh:
                for line in fh:
                    for formatted in format_log_line(line, style, raw=raw):
                        dest.write(formatted + "\n")
            if include_verify:
                vpath = verify_log_for(path)
                if vpath is not None:
                    dest.write("\n")
                    for formatted in _format_verify_section(vpath, style, raw=raw):
                        dest.write(formatted + "\n")
        dest.flush()
    except BrokenPipeError:
        pass
    finally:
        if pager_proc is not None:
            try:
                if pager_proc.stdin:
                    pager_proc.stdin.close()
            except OSError:
                pass
            pager_proc.wait()


def stream_logs(
    initial_files: list[Path],
    logs_dir: Path | None,
    *,
    target: str | None = None,
    batch_ids: set[str] | None = None,
    follow_new: bool = True,
    style: Style,
    raw: bool = False,
    include_verify: bool = False,
    poll_interval: float = 0.15,
    out=None,
    stop_event=None,
) -> None:
    """Suit un flux de logs en temps réel et bascule vers les nouveaux fichiers du lot."""
    out = out or sys.stdout
    while not initial_files:
        if stop_event is not None and stop_event.is_set():
            return
        if logs_dir is not None:
            found = find_matching_logs(logs_dir, target=target, batch_ids=batch_ids)
            if found:
                initial_files = [found[-1]] if (target is None and batch_ids is None) else found
                break
        time.sleep(poll_interval)

    # Affiche d'abord les fichiers antérieurs s'il y en a plusieurs, puis suit le dernier.
    if len(initial_files) > 1:
        render_logs_cold(initial_files[:-1], style, raw=raw, include_verify=include_verify, use_pager=False, out=out)
        out.write("\n")

    current = initial_files[-1]
    current_mtime = current.stat().st_mtime
    existing = find_matching_logs(logs_dir, target=target, batch_ids=batch_ids) if logs_dir is not None else initial_files
    seen_mtimes: dict[Path, float] = {}
    for p in existing:
        try:
            seen_mtimes[p] = p.stat().st_mtime
        except OSError:
            pass
    for p in initial_files:
        try:
            seen_mtimes[p] = p.stat().st_mtime
        except OSError:
            pass
    verify_shown: set[Path] = set()

    def emit_verify_if_ready(path: Path) -> None:
        if not include_verify or path in verify_shown:
            return
        vpath = verify_log_for(path)
        if vpath is not None:
            verify_shown.add(path)
            out.write("\n")
            for ln in _format_verify_section(vpath, style, raw=raw):
                out.write(ln + "\n")
            out.flush()

    out.write(_format_file_header(current, style) + "\n")
    out.flush()
    fh = current.open("r", errors="replace")
    buf = ""
    try:
        while True:
            try:
                if os.fstat(fh.fileno()).st_size < fh.tell():
                    fh.seek(0)
                    buf = ""
            except OSError:
                pass
            chunk = fh.read()
            if chunk:
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    for formatted in format_log_line(line, style, raw=raw):
                        out.write(formatted + "\n")
                out.flush()
                try:
                    current_mtime = current.stat().st_mtime
                    seen_mtimes[current] = current_mtime
                except OSError:
                    pass
                continue

            emit_verify_if_ready(current)

            if follow_new and logs_dir is not None:
                latest = find_matching_logs(logs_dir, target=target, batch_ids=batch_ids)
                newer: list[Path] = []
                for p in latest:
                    if p == current:
                        continue
                    try:
                        mt = p.stat().st_mtime
                    except OSError:
                        continue
                    prev_mt = seen_mtimes.get(p)
                    if prev_mt is None or mt > max(prev_mt, current_mtime):
                        newer.append(p)
                        seen_mtimes[p] = mt
                if newer:
                    # Vide un éventuel reliquat sans newline avant de basculer.
                    if buf:
                        for formatted in format_log_line(buf, style, raw=raw):
                            out.write(formatted + "\n")
                        buf = ""
                    emit_verify_if_ready(current)
                    fh.close()
                    for skipped in newer[:-1]:
                        out.write("\n")
                        render_logs_cold([skipped], style, raw=raw, include_verify=include_verify,
                                         use_pager=False, out=out)
                    current = newer[-1]
                    try:
                        current_mtime = current.stat().st_mtime
                        seen_mtimes[current] = current_mtime
                    except OSError:
                        pass
                    out.write("\n" + _format_file_header(current, style) + "\n")
                    out.flush()
                    fh = current.open("r", errors="replace")
                    continue

            if stop_event is not None and stop_event.is_set():
                if buf:
                    for formatted in format_log_line(buf, style, raw=raw):
                        out.write(formatted + "\n")
                    out.flush()
                emit_verify_if_ready(current)
                break
            time.sleep(poll_interval)
    finally:
        fh.close()
