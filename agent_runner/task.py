from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent

TASKS_DIR = ".agent-tasks"
FAILURE_MARKER = "<!-- agent-runner:failure-report -->"


class Fail(Exception):
    """Erreur fatale (message affiché, code retour 1)."""
