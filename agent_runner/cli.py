"""agent-runner : orchestrateur externe de file de tâches pour CLI d'agents IA."""
from __future__ import annotations

import argparse
import importlib
import sys

from agent_runner.adapters import ADAPTERS
from agent_runner.task import EFFORTS, Fail
from agent_runner.ui import _ansi


def _lazy(module: str, name: str):
    def _dispatch(args):
        mod = importlib.import_module(f"agent_runner.commands.{module}")
        return getattr(mod, name)(args)

    return _dispatch


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agent-runner", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="initialise .agent-tasks/ dans le dépôt courant")
    p.add_argument("--gitignore", action="store_true", help="utilise .gitignore au lieu de .git/info/exclude")
    p.add_argument("--with-agent-rules", action="store_true", help="ajoute les consignes à CLAUDE.md / AGENTS.md")
    p.add_argument("--update-config", action="store_true", help="complète un config.toml existant avec les nouvelles sections")
    p.set_defaults(func=_lazy("init", "cmd_init"))
    p = sub.add_parser("run", help="exécute les tâches de .agent-tasks/todo/")
    p.add_argument("--batch", help="ne traite que ce lot")
    p.add_argument("--dry-run", action="store_true", help="affiche le plan sans exécuter")
    p.add_argument("--resume", action="store_true", help="reprend en conservant les modifications partielles")
    p.add_argument("--continue-on-error", action="store_true", help="rollback vers failed/ et continue")
    p.add_argument("--model", help="force le modèle de départ")
    p.add_argument("--no-escalate", action="store_true", help="désactive l'escalade de modèle")
    p.add_argument("--avoid-runner", action="append", choices=sorted(ADAPTERS), metavar="RUNNER",
                   help="bascule d'emblée sur leur runner de repli les tâches de ce runner (ex. quota presque plein) ; "
                        "répétable")
    p.add_argument("--once", action="store_true", help="n'exécute que la prochaine tâche (équivalent à --limit 1)")
    p.add_argument("--limit", type=int, help="exécute au plus N tâches")
    p.set_defaults(func=_lazy("run", "cmd_run"))
    p = sub.add_parser("models", help="chaînes configurées et modèles disponibles (agy models)")
    p.add_argument("runner", nargs="?", choices=sorted(ADAPTERS))
    p.set_defaults(func=_lazy("models", "cmd_models"))
    p = sub.add_parser("status", help="avancement, taux d'escalade et coûts")
    p.set_defaults(func=_lazy("misc", "cmd_status"))
    p = sub.add_parser("report", help="détail par tentative (tokens, coût, durée, statut)")
    p.add_argument("--batch", help="filtre sur un lot")
    p.add_argument("--last", type=int, help="affiche les N dernières tentatives")
    p.add_argument("--json", action="store_true", help="sortie structurée au format JSON")
    p.set_defaults(func=_lazy("report", "cmd_report"))
    p = sub.add_parser("logs", help="affiche ou suit les journaux d'exécution formatés pour un humain")
    p.add_argument("target", nargs="?", help="identifiant de tâche (ou chemin de fichier)")
    p.add_argument("--file", dest="file", help="chemin vers un fichier de log spécifique")
    p.add_argument("--batch", help="affiche/suit les logs de toutes les tâches du lot")
    p.add_argument("-f", "--stream", action="store_true", help="suit les logs en temps réel")
    p.add_argument("--verify", action="store_true", help="inclut les fichiers .verify.log associés")
    p.add_argument("--raw", action="store_true", help="affiche les lignes brutes sans formatage")
    p.add_argument("--no-pager", action="store_true", help="désactive le pager (less -R)")
    p.add_argument("--no-color", action="store_true", help="désactive la coloration ANSI")
    p.set_defaults(func=_lazy("logs", "cmd_logs"))
    p = sub.add_parser("retry", help="remet une tâche (ou toutes avec --all) de failed/ dans todo/")
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true", help="remet toutes les tâches de failed/ dans todo/")
    p.set_defaults(func=_lazy("misc", "cmd_retry"))
    p = sub.add_parser("merge", help="fusionne un lot terminé (done/) dans sa base_branch")
    p.add_argument("--batch", help="lot à fusionner (obligatoire si plusieurs lots dans done/ sans --all)")
    p.add_argument("--all", action="store_true", help="fusionne tous les lots terminés dont la branche existe encore")
    p.add_argument("--strategy", choices=("no-ff", "ff", "squash"), default="no-ff",
                   help="stratégie de fusion (défaut : no-ff)")
    p.add_argument("--no-delete", action="store_true", help="conserve la branche après fusion")
    p.add_argument("--force", action="store_true", help="autorise la fusion même s'il reste des tâches dans todo/failed")
    p.add_argument("--dry-run", action="store_true", help="affiche le merge et le message sans exécuter")
    p.set_defaults(func=_lazy("merge", "cmd_merge"))
    p = sub.add_parser("clean", help="supprime logs/ (et done/ avec --all)")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=_lazy("misc", "cmd_clean"))
    p = sub.add_parser("create-plan", help="découpe un ou plusieurs documents (plan, spec, carto) en fiches todo/")
    p.add_argument("plans", nargs="+", help="chemin(s) vers le(s) fichier(s) plan / spec / cartographie")
    p.add_argument("--batch", help="nom du lot imposé aux fiches")
    p.add_argument("--branch", help="branche cible imposée aux fiches")
    p.add_argument("--base-branch", help="branche de base (défaut : main)")
    p.add_argument("--runner", choices=sorted(ADAPTERS), help="force un runner")
    p.add_argument("--model", help="force un modèle")
    p.add_argument("--effort", choices=EFFORTS, help="effort de raisonnement")
    p.set_defaults(func=_lazy("create_plan", "cmd_create_plan"))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Fail as exc:
        print(_ansi("1;31", "agent-runner:", sys.stderr) + f" {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nagent-runner: interrompu ; relancer avec --resume pour reprendre le travail partiel.",
              file=sys.stderr)
        return 130
