"""Commande models : inspection des modèles et capacités des runners."""

from __future__ import annotations

import json

from agent_runner.adapters import ADAPTERS
from agent_runner.config import DEFAULTS, load_config
from agent_runner.gitutil import repo_root
from agent_runner.probe import in_catalog, probe_runners
from agent_runner.routing import adapter_for
from agent_runner.task import Fail
from agent_runner.ui import _ansi


def cmd_models(args) -> int:
    """Affiche, par runner, la chaîne configurée, les capacités et le catalogue réellement disponible."""
    try:
        cfg = load_config(repo_root())
    except Fail:
        cfg = json.loads(json.dumps(DEFAULTS))
    runners = [args.runner] if args.runner else sorted(ADAPTERS)
    status, catalogs = probe_runners(cfg, runners)
    ok = [r for r in runners if not status[r]]
    print(f"runners disponibles : {', '.join(ok) or 'aucun'}")
    for r in runners:
        if status[r]:
            print(f"runner indisponible : {r} ({status[r]})")
    print(f"default_runner : {cfg['default_runner']}\n")
    for runner in runners:
        adapter = adapter_for(runner, cfg)
        chain = cfg["escalation"].get(runner) or []
        disp_status = _ansi("32", "(disponible)") if not status[runner] else _ansi("33", "(indisponible)")
        print(_ansi("1;36", f"[{runner}]") + f" {disp_status}")
        windows = None
        try:
            windows = adapter.get_usage_windows()
        except Exception:
            pass
        if windows:
            parts = []
            for w in windows:
                color = "32" if w.utilization < 50 else ("33" if w.utilization < 80 else "1;31")
                reset_info = f" (reset dans {w.reset_delay})" if w.reset_delay else ""
                parts.append(f"{w.name}: {_ansi(color, f'{w.utilization:.0f}%')}{reset_info}")
            print(_ansi("1;35", "  fenêtres d'usage : ") + " | ".join(parts))
        else:
            print(_ansi("2", "  fenêtres d'usage : non disponible (nécessite un compte OAuth connecté)"))
        tools_st = "oui" if adapter.supports_allowed_tools else "non"
        turns_st = "oui" if adapter.supports_max_turns and runner == "claude" else ("partiel" if runner == "codex" else "non")
        budget_st = "oui" if adapter.supports_max_budget else "non"
        print(_ansi("2", f"  capacités        : allowed_tools: {tools_st} | max_turns: {turns_st} | max_budget: {budget_st} | timeout: oui"))
        if adapter.recommended_usage:
            print(_ansi("2", f"  usage recommandé : {adapter.recommended_usage}"))
        print(f"  chaîne           : {' -> '.join(chain) or '-'}")
        if adapter.exclude:
            print(_ansi("2", f"  exclus           : {', '.join(adapter.exclude)}"))
        if status[runner]:
            print(_ansi("33", f"  motif indispo    : {status[runner]}"))
        if chain:
            print(_ansi("2", "  modèles configurés :"))
            for m in chain:
                info = adapter.get_model_info(m)
                if info:
                    meta = _ansi("2", f"[{info['level']:<4} | {info['context']:<4} | {info['cost']:<4}]")
                    desc = info.get("desc", "")
                    print(f"    * {m:<18} {meta} {desc}")
                else:
                    print(f"    * {m}")
        catalog = catalogs.get(runner)
        if catalog is not None:
            print(_ansi("2", f"  catalogue ({runner} models) :"))
            for family, efforts in catalog.items():
                mark = _ansi("32", "*") if family in chain else " "
                print(f"  {mark} {family:<34} {_ansi('2', ', '.join(efforts) or '(sans effort)')}")
            for m in chain:
                if not in_catalog(m, catalog):
                    print(_ansi("1;31", f"  ! {m} : absent du catalogue"))
        elif not status[runner]:
            print(_ansi("2", "  (pas de catalogue exposé par le CLI : alias de la chaîne utilisés tels quels)"))
        print()
    return 0
