"""Construction du prompt envoyé à l'agent pour une tâche (y compris escalade et reprise)."""

from __future__ import annotations

from pathlib import Path

from agent_runner.gitutil import git
from agent_runner.task import TASKS_DIR, Task


def _recent_task_logs(root: Path, task_id: str, limit: int = 4) -> list[str]:
    """Retourne les chemins relatifs des derniers `.verify.log` / `.log` d'une tâche."""
    logs_dir = root / TASKS_DIR / "logs"
    if not logs_dir.is_dir():
        return []
    matches = [p for p in logs_dir.glob(f"{task_id}.try*") if p.suffix == ".log"]
    matches.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return [f"{TASKS_DIR}/logs/{p.name}" for p in matches[:limit]]


def build_prompt(task: Task, root: Path, k: int, prev: dict | None, partial: str, head_before: str = "") -> str:
    parts = [
        f"Tu exécutes UNE tâche d'une file automatisée (id : {task.id}, branche {task.branch}, "
        "déjà checkoutée). Lis CLAUDE.md s'il existe."
    ]
    recent_logs = _recent_task_logs(root, task.id) if (prev or partial) else []
    logs_hint = (
        "Journaux précédents disponibles en lecture seule : " + ", ".join(recent_logs) + " "
        "(consulte-les ainsi que tout fichier de log détaillé indiqué par un script de vérification en mode quiet).\n"
        if recent_logs else ""
    )
    if prev:
        status = git("status", "--short", cwd=root).stdout.strip() or "(arbre propre)"
        stat = git("diff", "--stat", "HEAD", cwd=root).stdout.strip() or "(aucune différence)"
        committed = ""
        if head_before and git("rev-parse", "HEAD", cwd=root).stdout.strip() != head_before:
            log = git("log", "--oneline", f"{head_before}..HEAD", cwd=root, check=False).stdout.strip()
            cstat = git("diff", "--stat", f"{head_before}..HEAD", cwd=root, check=False).stdout.strip()
            committed = (f"Commits déjà créés par les tentatives précédentes (git log --oneline) :\n{log}\n"
                         f"git diff --stat {head_before[:8]}..HEAD :\n{cstat}\n")
        parts.append(
            f"ESCALADE : la tentative {k - 1} ({prev['model']}) a échoué.\n"
            f"Motif : {prev['reason']}\n"
            + (f"Sortie de la vérification :\n{prev['verify_tail']}\n" if prev.get("verify_tail") else "")
            + (f"Dernière sortie de l'agent :\n{prev['agent_tail']}\n" if prev.get("agent_tail") else "")
            + logs_hint
            + f"État actuel du dépôt (travail partiel CONSERVÉ — reprends-le, ne recommence pas) :\n"
            f"git status --short :\n{status}\ngit diff --stat HEAD :\n{stat}\n"
            + committed
            + "Corrige directement le blocage."
        )
    elif partial:
        parts.append(
            "REPRISE : une exécution précédente de cette tâche a été interrompue. L'arbre contient "
            f"son travail partiel non commité :\n{partial}\n"
            + logs_hint
            + "Inspecte-le (git diff) et termine-le plutôt que de recommencer ; ne l'annule que s'il est incohérent."
        )
    parts.append("## Tâche\n\n" + task.body.strip())
    commit_rule = (
        f"Une fois `{task.verify_cmd}` passé avec succès, crée UN seul commit, message en anglais au format conventionnel."
        if (task.require_commit and task.verify_cmd)
        else "Crée UN seul commit, message en anglais au format conventionnel."
        if task.require_commit
        else "Ne crée aucun commit : cette tâche ne modifie pas le dépôt (audit/baseline)."
    )
    rules = [
        "Réalise cette tâche, et uniquement elle (les tâches précédentes sont déjà commitées, "
        "les suivantes ne te concernent pas).",
        "Modifie les fichiers avec Edit/Write ; pour repérer des plages de lignes sans lire un fichier entier, "
        "utilise `file-outline <fichier>` ; pour déplacer un gros bloc de lignes entre fichiers, "
        "utilise `move-lines <src> <début> <fin> <dst>` plutôt que de réécrire le code.",
        "Ne modifie jamais .agent-tasks/ (lecture seule sur .agent-tasks/logs/ autorisée pour diagnostic) "
        "et n'écris rien hors du dépôt.",
        "Si tu es bloqué, ne commite rien de cassé et explique le blocage.",
    ]
    if task.verify_cmd:
        rules.insert(1, f"Avant de commiter, exécute `{task.verify_cmd}` et corrige jusqu'au succès "
                        "(le runner la réexécutera après ton travail ; si un script de check est en mode quiet/`-q` "
                        "et échoue sans détail, lis le fichier de log d'erreur qu'il indique ou relance-le sans `-q`).")
        rules.insert(2, commit_rule)
    else:
        rules.insert(1, commit_rule)
    parts.append("## Règles d'exécution\n\n" + "\n".join(f"- {r}" for r in rules))
    return "\n\n".join(parts)
