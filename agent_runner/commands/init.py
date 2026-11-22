"""Commande agent-runner init : initialisation d'un dépôt."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

from agent_runner.adapters import ADAPTERS
from agent_runner.config import DEFAULTS, load_config
from agent_runner.gitutil import branch_exists, git, repo_root
from agent_runner.probe import probe_runners
from agent_runner.task import Fail, TASKS_DIR, read_tool_resource

RULES_BEGIN = "<!-- BEGIN agent-task-runner -->"
RULES_END = "<!-- END agent-task-runner -->"


def detect_stack(root: Path) -> tuple[str, list[str]]:
    verify, tools = "", []
    checks = sorted((root / "tools").glob("check-*.sh")) if (root / "tools").is_dir() else []
    if checks:
        verify = f"tools/{checks[0].name}"
        tools += [f"Bash(tools/{c.name}:*)" for c in checks]
    if (root / "Cargo.toml").exists():
        verify = verify or "cargo test --quiet"
        tools.append("Bash(cargo:*)")
    if (root / "gradlew").exists():
        verify = verify or "./gradlew test"
        tools.append("Bash(./gradlew:*)")
    if (root / "pyproject.toml").exists():
        verify = verify or "python -m pytest -q"
        tools += ["Bash(python:*)", "Bash(pytest:*)"]
    if (root / "package.json").exists():
        verify = verify or "npm test --silent"
        tools += ["Bash(npm:*)", "Bash(npx:*)"]
    return verify, tools


def render_config(main: str, verify: str, tools: list[str], default_runner: str = "claude") -> str:
    j = json.dumps
    allowed = list(DEFAULTS["default_allowed_tools"]) + [t for t in tools if t not in DEFAULTS["default_allowed_tools"]]
    esc = "\n".join(f"{name} = {j(chain)}" for name, chain in DEFAULTS["escalation"].items())
    excl = "\n".join(f"{name} = {j(pats)}" for name, pats in DEFAULTS["exclude_models"].items())
    fmap = "\n\n".join(f"[fallback_map.{dst}]\n" + "\n".join(f"{j(k)} = {j(v)}" for k, v in m.items())
                       for dst, m in DEFAULTS["fallback_map"].items())
    return f"""# Configuration agent-runner (projet). Surcharge ~/.config/agent-task-runner/config.toml.
main_branch = {j(main)}
default_runner = {j(default_runner)}   # choisi par `agent-runner init` parmi les runners installés et connectés
# runners = {j(list(ADAPTERS))}   # runners autorisés (défaut : tous) ; les autres sont traités comme indisponibles
auto_fallback = true   # fiche sans fallback_runner : repli sur l'autre runner (`fallback_runner: none` l'interdit)
default_verify_cmd = {j(verify)}
default_allowed_tools = {j(allowed, ensure_ascii=False)}
default_max_turns = {DEFAULTS["default_max_turns"]}
default_timeout_sec = {DEFAULTS["default_timeout_sec"]}
permission = "accept"   # "accept" (acceptEdits) ou "bypass" (--dangerously-skip-permissions)
strict_allowed_tools = false   # true : pas de repli auto vers un runner qui ignore allowed_tools (agy, codex)
# on_finish_cmd = 'notify-send agent-runner "$AGENT_RUNNER_STATUS : $AGENT_RUNNER_DONE/$AGENT_RUNNER_TOTAL"'
#   lancé en fin de `run` ; variables AGENT_RUNNER_{{STATUS,EXIT_CODE,TOTAL,DONE,FAILED,DONE_IDS,FAILED_IDS,REPO}}

# Chaînes d'escalade, du modèle le moins cher au plus puissant.
# agy : familles sans suffixe d'effort ; le runner compose `<famille>-<effort>` d'après `agy models`
# (`agent-runner models` affiche le catalogue disponible).
[escalation]
{esc}

# Modèles à ne jamais utiliser via un CLI (motifs fnmatch).
[exclude_models]
{excl}

# Équivalences lors d'une bascule vers le runner de repli (table = runner de destination).
{fmap}

# Arguments CLI supplémentaires par runner.
[extra_args]
# claude = []
# agy = []
# codex = []

# Routage dynamique par niveau (fiches avec `level: N1|N2|N3` sans `runner` épinglé) et seuils de quota.
[routing.level_runner]
N1 = "agy"
N2 = "agy"
N3 = "claude"

[routing.quota_threshold]
claude = 80.0
agy = 80.0
codex = 80.0

[routing]
reset_grace_min = 20       # relâche le seuil si le reset est imminent (<= N min) et quota < 95%
usage_refresh_sec = 300    # intervalle minimal (s) entre deux interrogations de quota en cours de run
usage_refresh_tasks = 5    # rafraîchissement paresseux toutes les N tâches
"""


def choose_default_runner(current: str, status: dict[str, str | None]) -> str | None:
    """Garde `current` s'il est utilisable, sinon le premier runner utilisable (ordre de ADAPTERS)."""
    if not status.get(current):
        return current
    return next((r for r in ADAPTERS if not status.get(r)), None)


def set_toml_key(text: str, key: str, value: str) -> str:
    """Fixe une clé de premier niveau d'un TOML en préservant le reste du fichier (commentaires compris)."""
    literal = json.dumps(value)
    pat = re.compile(rf'^({re.escape(key)}\s*=\s*)("[^"]*"|\S+)', re.M)
    # Une clé de premier niveau précède toute table : on ne cherche qu'avant le premier `[table]`.
    first_table = re.search(r"^\s*\[", text, re.M)
    head_end = first_table.start() if first_table else len(text)
    m = pat.search(text, 0, head_end)
    if m:
        return text[: m.start(2)] + literal + text[m.end(2):]
    line = f"{key} = {literal}\n"
    if first_table:
        return text[:head_end] + line + "\n" + text[head_end:]
    return text + ("" if not text or text.endswith("\n") else "\n") + line


def configure_default_runner(root: Path, cfg_path: Path) -> str | None:
    """Vérifie les runners (PATH + connexion) et retourne le default_runner à utiliser (None si aucun)."""
    try:
        cfg = load_config(root)
    except Fail as exc:
        print(f"avertissement : {exc} ; détection des runners avec la configuration par défaut", file=sys.stderr)
        cfg = json.loads(json.dumps(DEFAULTS))
    status, _ = probe_runners(cfg, ADAPTERS)
    for r in ADAPTERS:
        print(f"runner {r} : " + (f"indisponible ({status[r]})" if status[r] else "installé et connecté"))
    current = cfg["default_runner"] if cfg.get("default_runner") in ADAPTERS else "claude"
    chosen = choose_default_runner(current, status)
    if chosen is None:
        print("avertissement : aucun runner utilisable ; default_runner inchangé "
              f"({current}). Installer/connecter {' ou '.join(ADAPTERS)} puis relancer `agent-runner init`.", file=sys.stderr)
        return None
    if not cfg_path.exists():
        return chosen
    text = cfg_path.read_text()
    if chosen != current:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            print(f"avertissement : {cfg_path} invalide, default_runner non modifié", file=sys.stderr)
            return chosen
        cfg_path.write_text(set_toml_key(text, "default_runner", chosen))
        print(f"default_runner : {current} -> {chosen} ({current} {status[current]})")
    else:
        print(f"default_runner : {chosen} (inchangé)")
    return chosen


def agent_rules_block() -> str:
    return (
        f"{RULES_BEGIN}\n"
        "## agent-task-runner\n\n"
        "- Ne modifie jamais `.agent-tasks/` (file de tâches gérée par `agent-runner`).\n"
        "- Utilise `file-outline <fichier>` pour repérer les plages de lignes exactes avant de lire un fichier entier.\n"
        "- Pour déplacer un gros bloc de lignes (300+) entre fichiers, utilise "
        "`move-lines <src> <début> <fin> <dst>` au lieu de réécrire le code.\n"
        f"{RULES_END}\n"
    )


def inject_rules(path: Path, create: bool) -> bool:
    if not path.exists():
        if not create:
            return False
        path.write_text(agent_rules_block())
        return True
    text = path.read_text()
    block = agent_rules_block()
    pat = re.compile(re.escape(RULES_BEGIN) + r".*?" + re.escape(RULES_END) + r"\n?", re.S)
    new = pat.sub(lambda _m: block, text) if pat.search(text) else text.rstrip("\n") + "\n\n" + block
    if new != text:
        path.write_text(new)
    return True


def update_config_file(cfg_path: Path) -> list[str]:
    """Ajoute les sections `[routing*]` manquantes dans un config.toml existant sans écraser l'existant."""
    text = cfg_path.read_text()
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise Fail(f"{cfg_path} : TOML invalide : {exc}") from exc
    routing = parsed.get("routing") if isinstance(parsed.get("routing"), dict) else {}
    added: list[str] = []
    chunks: list[str] = []
    if "level_runner" not in routing:
        added.append("routing.level_runner")
        chunks.append(
            "\n# Routage dynamique par niveau (fiches avec `level: N1|N2|N3` sans `runner` épinglé)\n"
            "[routing.level_runner]\nN1 = \"agy\"\nN2 = \"agy\"\nN3 = \"claude\"\n"
        )
    if "quota_threshold" not in routing:
        added.append("routing.quota_threshold")
        chunks.append(
            "\n[routing.quota_threshold]\nclaude = 80.0\nagy = 80.0\ncodex = 80.0\n"
        )
    if not any(k in routing for k in ("reset_grace_min", "usage_refresh_sec", "usage_refresh_tasks")):
        added.append("routing")
        chunks.append(
            "\n[routing]\n"
            "reset_grace_min = 20       # relâche le seuil si le reset est imminent (<= N min) et quota < 95%\n"
            "usage_refresh_sec = 300    # intervalle minimal (s) entre deux interrogations de quota en cours de run\n"
            "usage_refresh_tasks = 5    # rafraîchissement paresseux toutes les N tâches\n"
        )
    if chunks:
        sep = "" if not text or text.endswith("\n") else "\n"
        cfg_path.write_text(text + sep + "".join(chunks))
    return added


def cmd_init(args) -> int:
    root = repo_root()
    base = root / TASKS_DIR
    for d in ("todo", "done", "failed", "logs"):
        (base / d).mkdir(parents=True, exist_ok=True)
    example = base / "todo" / "00-example.md.disabled"
    if not example.exists():
        example.write_text(read_tool_resource("templates", "example-task.md"), encoding="utf-8")

    if args.gitignore:
        target = root / ".gitignore"
    else:
        target = Path(git("rev-parse", "--git-path", "info/exclude", cwd=root).stdout.strip())
        if not target.is_absolute():
            target = root / target
        target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text() if target.exists() else ""
    if not any(ln.strip() in (".agent-tasks/", ".agent-tasks", "/.agent-tasks/", "/.agent-tasks") for ln in existing.splitlines()):
        with target.open("a") as fh:
            fh.write(("" if not existing or existing.endswith("\n") else "\n") + ".agent-tasks/\n")

    cfg_path = base / "config.toml"
    default_runner = configure_default_runner(root, cfg_path)
    if not cfg_path.exists():
        main = next((b for b in ("main", "master") if branch_exists(root, b)), "main")
        verify, tools = detect_stack(root)
        cfg_path.write_text(render_config(main, verify, tools, default_runner or "claude"))
        print(f"config.toml généré (default_runner : {default_runner or 'claude'}, verify par défaut : {verify or '-'})")
    elif args.update_config:
        added = update_config_file(cfg_path)
        if added:
            print(f"config.toml complété ({', '.join(added)})")
        else:
            print("config.toml déjà à jour")
    if args.with_agent_rules:
        inject_rules(root / "CLAUDE.md", create=True)
        inject_rules(root / "AGENTS.md", create=False)
        print("consignes ajoutées à CLAUDE.md (et AGENTS.md s'il existe)")
    print(f"{TASKS_DIR}/ initialisé dans {root}")
    return 0
