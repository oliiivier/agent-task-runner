# agent-task-runner

Orchestrateur externe léger qui exécute une file de tâches de développement de manière autonome, séquentielle et reproductible via des CLI d'agents IA (`claude -p`, `agy`).

Chaque tâche démarre sur le modèle le moins cher (`haiku`) et n'escalade (`haiku → sonnet → opus → fable`) qu'en cas d'échec, en reprenant le travail partiel. Seul le runner valide les post-conditions (code retour, arbre propre, `HEAD` avancé, `verify_cmd`) et déplace les tâches vers `done/` ou `failed/`.

Spécifications complètes : [`docs/specification.md`](docs/specification.md).

## Installation

Python 3.11+ (bibliothèque standard uniquement) et Git.

```bash
./install.sh              # symlinks : ~/.local/bin/{agent-runner,move-lines}, ~/.claude/skills/plan-tasks, ~/.gemini/config/skills/plan-tasks
./install.sh --uninstall
```

## Utilisation

```bash
cd mon-projet
agent-runner init [--gitignore] [--with-agent-rules]   # crée .agent-tasks/ (ignoré par Git)
agent-runner models                                    # runners disponibles (installés + connectés) et modèles
# générer les tâches avec le skill plan-tasks, ou copier .agent-tasks/todo/00-example.md.disabled
agent-runner run --dry-run                              # plan + chaînes d'escalade
agent-runner run [--batch X] [--resume] [--continue-on-error] [--model M] [--no-escalate]
agent-runner status
agent-runner retry <id>   # ou --all
agent-runner clean [--all]
```

Une tâche est un fichier `.agent-tasks/todo/NN-slug.md` (frontmatter YAML + corps autonome) ; voir [`templates/example-task.md`](templates/example-task.md) et [`schemas/task.schema.json`](schemas/task.schema.json). Configuration : `.agent-tasks/config.toml` (modèle : [`templates/config.toml`](templates/config.toml)).

## Organisation

| Chemin | Rôle |
| :--- | :--- |
| `bin/agent-runner` | CLI (`init`, `run`, `models`, `status`, `retry`, `clean`) |
| `adapters/` | adaptateurs `claude` (flux `stream-json`, plafond `max_turns` appliqué par le runner) et `agy` |
| `helpers/move-lines` | déplace une plage de lignes entre fichiers sans régénérer le code |
| `skills/` | skill `plan-tasks`, commun à Claude Code et `agy` (même `SKILL.md`) |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Les tests utilisent des dépôts Git temporaires et un faux `claude` placé dans le `PATH`.

## Licence

[MIT](LICENSE)

## Limites connues

- `codex` (optionnel, sans effet s'il est absent) : `codex exec --json` ; ni liste blanche d'outils ni budget, `max_turns` approché ; chaîne `[escalation].codex` à adapter aux modèles proposés. Après installation de Codex, relancer `./install.sh` pour installer la skill dans `~/.codex/skills`.
- `agy` : ni liste blanche d'outils, ni `max_turns`, ni budget ne sont transmis (non exposés par `agy --help`) ; le format JSON de sortie étant non documenté, l'analyse des métriques est tolérante. Les alias de la chaîne `[escalation].agy` sont à adapter (`agy models`).
- `--with-agent-rules` écrit dans `CLAUDE.md` (créé si absent) et `AGENTS.md` s'il existe.
