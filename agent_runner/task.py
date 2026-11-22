"""Modèle de tâche et parsing du frontmatter."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import zipfile

from agent_runner.adapters import ADAPTERS
from agent_runner.frontmatter import parse_frontmatter

TOOL_ROOT = Path(__file__).resolve().parent.parent


def read_tool_resource(*parts: str, default: str | None = None) -> str:
    """Lit une ressource embarquée (dépôt local ou archive zipapp)."""
    path = TOOL_ROOT.joinpath(*parts)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if TOOL_ROOT.is_file() and zipfile.is_zipfile(TOOL_ROOT):
        member = "/".join(parts)
        with zipfile.ZipFile(TOOL_ROOT) as zf:
            if member in zf.namelist():
                return zf.read(member).decode("utf-8")
    if default is not None:
        return default
    raise FileNotFoundError(f"Ressource introuvable : {'/'.join(parts)}")

TASKS_DIR = ".agent-tasks"
FAILURE_MARKER = "<!-- agent-runner:failure-report -->"


class Fail(Exception):
    """Erreur fatale (message affiché, code retour 1)."""


MERGE_STRATEGIES = ("none", "auto_ff", "auto_no_ff")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
LEVELS = ("N1", "N2", "N3")
FIELDS = {
    "id", "batch", "branch", "base_branch", "merge_strategy", "level", "runner", "prefer_runner",
    "fallback_runner", "model", "effort", "escalate", "allow_premium", "max_model", "verify_cmd",
    "require_commit", "allowed_tools", "max_turns", "timeout_sec", "max_budget_usd",
    "verify_timeout_sec", "verify_retries",
}


@dataclass
class Task:
    path: Path
    id: str
    branch: str
    body: str
    batch: str = "default"
    base_branch: str = "main"
    merge_strategy: str = "none"
    level: str | None = None
    runner: str = "claude"
    runner_pinned: bool = False
    prefer_runner: str | None = None
    fallback_runner: str | None = None
    fallback_auto: bool = False  # repli déduit (champ absent + auto_fallback), pas écrit dans la fiche
    model: str | None = None
    effort: str | None = None
    escalate: bool = True
    allow_premium: bool = True
    max_model: str | None = None
    verify_cmd: str = ""
    require_commit: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 35
    timeout_sec: int = 900
    max_budget_usd: float | None = None
    verify_timeout_sec: int | None = None  # délai de `verify_cmd` ; None : `timeout_sec`
    verify_retries: int = 1  # corrections sur le même modèle après un `verify_cmd` en échec, avant escalade


def parse_task(path: Path, cfg: dict) -> Task:
    try:
        meta, body = parse_frontmatter(path.read_text())
        unknown = set(meta) - FIELDS
        if unknown:
            raise ValueError(f"champs inconnus : {', '.join(sorted(unknown))}")
        for req in ("id", "branch"):
            if not meta.get(req):
                raise ValueError(f"champ obligatoire manquant : {req}")

        def get(name, default, typ):
            v = meta.get(name)
            if v is None:
                return default
            if typ is float and isinstance(v, int) and not isinstance(v, bool):
                v = float(v)
            if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
                raise ValueError(f"{name} : type invalide ({typ.__name__} attendu)")
            return v

        for name in ("id", "batch", "branch"):  # `id: 7` : identifiant numérique, pas un type invalide
            if isinstance(meta.get(name), int) and not isinstance(meta[name], bool):
                meta[name] = str(meta[name])
        tools = meta.get("allowed_tools")
        if tools is None:
            tools = list(cfg["default_allowed_tools"])
        elif not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise ValueError("allowed_tools : liste de chaînes attendue")
        level = get("level", None, str)
        if level is not None and level not in LEVELS:
            raise ValueError(f"level : {level!r} (attendu : {', '.join(LEVELS)})")
        runner_pinned = meta.get("runner") is not None
        default_r = cfg["default_runner"]
        if not runner_pinned and level:
            lvl_r = cfg.get("routing", {}).get("level_runner", {}).get(level)
            if lvl_r in ADAPTERS:
                default_r = lvl_r
        # La préférence souple prime sur le niveau pour le runner nominal (repli auto et validation du repli).
        if not runner_pinned and meta.get("prefer_runner") in ADAPTERS:
            default_r = meta["prefer_runner"]
        task = Task(
            path=path,
            id=str(get("id", "", str)),
            branch=get("branch", "", str),
            body=body,
            batch=get("batch", "default", str),
            base_branch=get("base_branch", cfg["main_branch"], str),
            merge_strategy=get("merge_strategy", "none", str),
            level=level,
            runner=get("runner", default_r, str),
            runner_pinned=runner_pinned,
            prefer_runner=get("prefer_runner", None, str),
            fallback_runner=get("fallback_runner", None, str),
            model=get("model", None, str),
            effort=get("effort", None, str),
            escalate=get("escalate", True, bool),
            allow_premium=get("allow_premium", True, bool),
            max_model=get("max_model", None, str),
            verify_cmd=get("verify_cmd", cfg["default_verify_cmd"], str),
            require_commit=get("require_commit", True, bool),
            allowed_tools=tools,
            max_turns=get("max_turns", cfg["default_max_turns"], int),
            timeout_sec=get("timeout_sec", cfg["default_timeout_sec"], int),
            max_budget_usd=get("max_budget_usd", None, float),
            verify_timeout_sec=get("verify_timeout_sec", None, int),
            verify_retries=get("verify_retries", cfg["default_verify_retries"], int),
        )
        if task.merge_strategy not in MERGE_STRATEGIES:
            raise ValueError(f"merge_strategy : {task.merge_strategy!r} (attendu : {', '.join(MERGE_STRATEGIES)})")
        if task.runner not in ADAPTERS:
            raise ValueError(f"runner : {task.runner!r} (attendu : {', '.join(ADAPTERS)})")
        if task.prefer_runner is not None and task.prefer_runner not in ADAPTERS:
            raise ValueError(f"prefer_runner : {task.prefer_runner!r} (attendu : {', '.join(ADAPTERS)})")
        if task.fallback_runner == "none":  # repli explicitement interdit
            task.fallback_runner = None
        elif task.fallback_runner is None:
            if not isinstance(cfg.get("auto_fallback"), bool):
                raise Fail(f"config `auto_fallback` : booléen attendu (reçu {cfg.get('auto_fallback')!r})")
            if cfg["auto_fallback"]:
                task.fallback_runner = next((r for r in ADAPTERS if r != task.runner), None)
                task.fallback_auto = task.fallback_runner is not None
        elif (
            task.fallback_runner not in ADAPTERS or task.fallback_runner == task.runner
        ):
            raise ValueError(f"fallback_runner : {task.fallback_runner!r} (runner différent de {task.runner!r} "
                             f"attendu parmi : {', '.join(ADAPTERS)}, ou `none`)")
        if task.effort is not None and task.effort not in EFFORTS:
            raise ValueError(f"effort : {task.effort!r} (attendu : {', '.join(EFFORTS)})")
        if task.max_turns < 1 or task.timeout_sec < 1:
            raise ValueError("max_turns et timeout_sec doivent être >= 1")
        if task.verify_timeout_sec is not None and task.verify_timeout_sec < 1:
            raise ValueError("verify_timeout_sec doit être >= 1")
        if task.verify_retries < 0:
            raise ValueError("verify_retries doit être >= 0")
        chain = cfg["escalation"].get(task.runner) or []
        if task.max_model and chain and task.max_model not in chain:
            raise ValueError(f"max_model {task.max_model!r} absent de la chaîne {chain}")
        return task
    except ValueError as exc:
        raise Fail(f"{path.name} : {exc}") from exc
