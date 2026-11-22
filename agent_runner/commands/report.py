"""Commande report : affichage et analyse des métriques d'exécution."""

from __future__ import annotations

import json
from pathlib import Path
import re

from agent_runner.adapters import ADAPTERS
from agent_runner.config import load_config
from agent_runner.frontmatter import parse_frontmatter
from agent_runner.gitutil import repo_root
from agent_runner.routing import adapter_for
from agent_runner.task import TASKS_DIR, Fail
from agent_runner.ui import _ansi, _fmt_dur, _fmt_int


_FILE_PATH_RE = re.compile(r"(?:`([^`\s]+)`|\b([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+|[A-Za-z0-9_.-]+\.(?:py|kt|kts|rs|ts|tsx|js|jsx|go|java|c|cpp|h|tf|tfvars|yml|yaml|json|toml|md|sql|sh))\b)")


def _extract_task_files(body: str) -> set[str]:
    """Extrait les chemins de fichiers mentionnés dans une fiche (en priorité `## Périmètre exact`)."""
    scope_lines: list[str] = []
    in_scope = False
    for line in body.splitlines():
        s = line.strip()
        if s.lower().startswith("## périmètre"):
            in_scope = True
            continue
        if in_scope and s.startswith("## "):
            break
        if in_scope:
            scope_lines.append(line)
    text = "\n".join(scope_lines) if scope_lines else body
    files: set[str] = set()
    for m in _FILE_PATH_RE.finditer(text):
        cand = m.group(1) or m.group(2)
        if cand and ("/" in cand or "." in cand) and not cand.startswith(("http://", "https://")):
            files.add(cand.lstrip("./"))
    return files


def _load_task_files(base: Path) -> dict[str, set[str]]:
    """Associe chaque `task_id` aux fichiers cités dans `.agent-tasks/{done,failed,todo}`."""
    mapping: dict[str, set[str]] = {}
    for folder in ("done", "failed", "todo"):
        d = base / folder
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                meta, body = parse_frontmatter(p.read_text())
            except ValueError:
                continue
            tid = str(meta.get("id") or p.stem)
            mapping[tid] = _extract_task_files(body)
    return mapping


def _row_to_attempt(r: list[str]) -> dict:
    return {
        "timestamp": r[0], "batch": r[1], "task_id": r[2], "attempt": int(r[3]),
        "runner": r[4], "model": r[5], "turns": int(r[6]),
        "tokens_in": int(r[7]), "cache_write": int(r[8]), "cache_read": int(r[9]), "tokens_out": int(r[10]),
        "cost_usd": round(float(r[11]), 4), "duration_sec": int(float(r[12])), "status": r[13],
    }


def summarize_savings(attempts: list[dict], cfg: dict, task_files: dict[str, set[str]] | None = None) -> dict:
    """Calcule la répartition par palier, la borne basse tout-N3 et le scénario session unique avec cache."""
    by_level = {"N1": 0, "N2": 0, "N3": 0}
    actual_total = 0.0
    billed_total = 0.0
    baseline_total = 0.0
    single_total = 0.0
    seen_files: set[str] = set()
    cum_ctx = 0
    compaction_cap = 150_000  # plafond réaliste avant compaction en session unique
    overlaps: list[float] = []
    for a in attempts:
        runner = a["runner"]
        billed_total += a["cost_usd"]
        files = (task_files or {}).get(a["task_id"], set())
        overlap = (len(files & seen_files) / len(files)) if (files and seen_files) else 0.0
        if files:
            overlaps.append(overlap)
        seen_files |= files
        if runner in ADAPTERS:
            adapter = adapter_for(runner, cfg)
            info = adapter.get_model_info(a["model"]) or {}
            lvl = (info.get("level") or "").rstrip("+")
            if lvl in by_level:
                by_level[lvl] += 1
            p_actual = adapter.get_model_pricing(a["model"])
            actual = a["cost_usd"] if a["cost_usd"] > 0 else (
                adapter.estimate_cost(p_actual, a["tokens_in"], a["cache_write"], a["cache_read"], a["tokens_out"])
                if p_actual else 0.0
            )
            p_base = adapter.get_model_pricing(adapter.baseline_model) if adapter.baseline_model else None
            if p_base:
                baseline = adapter.estimate_cost(p_base, a["tokens_in"], a["cache_write"], a["cache_read"], a["tokens_out"])
                fresh_in = int(a["tokens_in"] * (1.0 - overlap))
                fresh_cw = int(a["cache_write"] * (1.0 - overlap))
                shared_cr = int((a["tokens_in"] + a["cache_write"]) * overlap)
                cum_cr = a["turns"] * cum_ctx
                single = adapter.estimate_cost(p_base, fresh_in, fresh_cw, a["cache_read"] + shared_cr + cum_cr, a["tokens_out"])
                cum_ctx = min(compaction_cap, cum_ctx + fresh_in + fresh_cw + a["tokens_out"])
            else:
                baseline = actual
                single = actual
        else:
            actual = a["cost_usd"]
            baseline = actual
            single = actual
        actual_total += actual
        baseline_total += baseline
        single_total += single
    saved = max(0.0, baseline_total - actual_total)
    saved_pct = round(100.0 * saved / baseline_total, 1) if baseline_total > 0 else 0.0
    avg_overlap_pct = round(100.0 * sum(overlaps) / len(overlaps), 1) if overlaps else 0.0
    return {
        "by_level": by_level,
        "billed_cost_usd": round(billed_total, 4),
        "actual_cost_usd": round(actual_total, 4),
        "baseline_n3_cost_usd": round(baseline_total, 4),
        "single_session_n3_cost_usd": round(single_total, 4),
        "avg_overlap_pct": avg_overlap_pct,
        "saved_usd": round(saved, 4),
        "saved_pct": saved_pct,
    }


def _print_savings(sav: dict) -> None:
    lvl = sav["by_level"]
    dist = ", ".join(f"{lvl[k]} {k}" for k in ("N1", "N2", "N3") if lvl[k])
    if dist:
        print(_ansi("2", f"Paliers : {dist}"))
    if sav["baseline_n3_cost_usd"] > 0 and sav["saved_usd"] > 0:
        billed = sav.get("billed_cost_usd", 0.0)
        billed_note = (f" (dont ${billed:.2f} facturé)"
                       if billed > 0 and abs(sav["actual_cost_usd"] - billed) >= 0.01 else "")
        print(_ansi("2", f"Économie estimée : ${sav['actual_cost_usd']:.2f} réel estimé{billed_note} vs "
                         f"~${sav['baseline_n3_cost_usd']:.2f} en tout-N3 (-{sav['saved_pct']:.0f} %, "
                         "borne basse à tokens identiques ; le gain réel est encore supérieur "
                         "grâce à l'absence de cumul de contexte)"))
    if sav.get("single_session_n3_cost_usd", 0) > 0 and sum(lvl.values()) > 1:
        print(_ansi("2", f"Session unique N3 (modélisée, recouvrement fichiers {sav['avg_overlap_pct']:.0f} %, "
                         f"plafond compaction 150k) : ~${sav['single_session_n3_cost_usd']:.2f}"))


def cmd_report(args) -> int:
    """Affiche le détail des tentatives journalisées dans usage.tsv."""
    root = repo_root()
    base = root / TASKS_DIR
    if not base.is_dir():
        raise Fail("`.agent-tasks/` introuvable : lancer `agent-runner init`")
    cfg = load_config(root)
    usage = base / "logs" / "usage.tsv"
    as_json = getattr(args, "json", False)
    rows = [ln.split("\t") for ln in usage.read_text().splitlines()[1:] if ln.strip()] if usage.exists() else []
    if args.batch:
        rows = [r for r in rows if r[1] == args.batch]
    if args.last and args.last > 0:
        rows = rows[-args.last:]
    if not rows:
        if as_json:
            empty_tot = {"count": 0, "turns": 0, "tokens_in": 0, "cache_write": 0,
                         "cache_read": 0, "tokens_out": 0, "cost_usd": 0.0, "duration_sec": 0}
            print(json.dumps({"attempts": [], "total": empty_tot, "savings": summarize_savings([], cfg)},
                             ensure_ascii=False, indent=2))
            return 0
        print(_ansi("2", f"Aucune exécution journalisée" + (f" pour le lot {args.batch!r}." if args.batch else ".")))
        return 0

    parsed = [_row_to_attempt(r) for r in rows]
    tot_in = sum(p["tokens_in"] for p in parsed)
    tot_cw = sum(p["cache_write"] for p in parsed)
    tot_cr = sum(p["cache_read"] for p in parsed)
    tot_out = sum(p["tokens_out"] for p in parsed)
    tot_turns = sum(p["turns"] for p in parsed)
    tot_dur = sum(p["duration_sec"] for p in parsed)
    tot_cost = sum(p["cost_usd"] for p in parsed)
    savings = summarize_savings(parsed, cfg, _load_task_files(base))
    if as_json:
        total = {
            "count": len(parsed), "turns": tot_turns,
            "tokens_in": tot_in, "cache_write": tot_cw, "cache_read": tot_cr, "tokens_out": tot_out,
            "cost_usd": round(tot_cost, 4), "duration_sec": tot_dur,
        }
        print(json.dumps({"attempts": parsed, "total": total, "savings": savings}, ensure_ascii=False, indent=2))
        return 0

    id_w = max(len("Tâche"), *(len(p["task_id"]) for p in parsed))
    mod_w = max(len("Modèle"), *(len(f"{p['runner']} {p['model']}") for p in parsed))
    header = (f"{'Tâche':<{id_w}}  {'#':>2}  {'Modèle':<{mod_w}}  {'Tours':>5}  "
              f"{'Entrée':>9}  {'Cache W':>9}  {'Cache R':>9}  {'Sortie':>9}  {'Coût':>7}  {'Durée':>6}  Statut")
    print(_ansi("1", header))
    print(_ansi("2", "-" * len(header)))
    for p in parsed:
        st = p["status"]
        st_color = "1;32" if st == "success" else "33" if st in ("quota", "max_turns", "timeout") else "1;31"
        model_col = f"{p['runner']} {p['model']}"
        print(f"{p['task_id']:<{id_w}}  {p['attempt']:>2}  {model_col:<{mod_w}}  {p['turns']:>5}  "
              f"{_fmt_int(p['tokens_in']):>9}  {_fmt_int(p['cache_write']):>9}  {_fmt_int(p['cache_read']):>9}  "
              f"{_fmt_int(p['tokens_out']):>9}  ${p['cost_usd']:>6.2f}  {_fmt_dur(p['duration_sec']):>6}  "
              + _ansi(st_color, st))
    print(_ansi("2", "-" * len(header)))
    summary = f"Total ({len(parsed)} tentative(s))"
    print(_ansi("1", f"{summary:<{id_w + 6 + mod_w}}  {tot_turns:>5}  "
                     f"{_fmt_int(tot_in):>9}  {_fmt_int(tot_cw):>9}  {_fmt_int(tot_cr):>9}  "
                     f"{_fmt_int(tot_out):>9}  ${tot_cost:>6.2f}  {_fmt_dur(tot_dur):>6}"))
    _print_savings(savings)
    return 0
