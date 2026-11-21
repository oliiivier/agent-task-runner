# agent-task-runner

Orchestrateur externe léger qui exécute une file de tâches de développement de manière autonome, séquentielle et reproductible via des CLI d'agents IA (`claude -p`, `agy`).

Chaque tâche démarre sur le modèle le moins cher (`haiku`) et n'escalade (`haiku → sonnet → opus → fable`) qu'en cas d'échec, en reprenant le travail partiel. Seul le runner valide les post-conditions (code retour, arbre propre, `HEAD` avancé, `verify_cmd`) et déplace les tâches vers `done/` ou `failed/`.

Spécifications complètes : [`docs/specification.md`](docs/specification.md).

## Installation

Python 3.11+ (bibliothèque standard uniquement) et Git.

```bash
./install.sh              # symlinks : ~/.local/bin/{agent-runner,move-lines,file-outline,replace-block}, skills dans ~/.claude/skills et ~/.gemini/config/skills
./install.sh --uninstall
```

## Utilisation

```bash
cd mon-projet
agent-runner init [--gitignore] [--with-agent-rules] [--update-config]   # crée ou complète .agent-tasks/
agent-runner models                                    # runners disponibles (installés + connectés) et modèles
# générer les tâches avec le skill plan-tasks, ou copier .agent-tasks/todo/00-example.md.disabled
agent-runner run --dry-run                              # plan + chaînes d'escalade
agent-runner run [--batch X] [--resume] [--continue-on-error] [--model M] [--no-escalate] [--avoid-runner R]
agent-runner status
agent-runner report [--batch X] [--last N]
agent-runner retry <id>   # ou --all
agent-runner merge [--batch X | --all] [--strategy no-ff|ff|squash] [--no-delete] [--dry-run]
agent-runner clean [--all]
```

Une tâche est un fichier `.agent-tasks/todo/NN-slug.md` (frontmatter YAML + corps autonome) ; voir [`templates/example-task.md`](templates/example-task.md) et [`schemas/task.schema.json`](schemas/task.schema.json). Configuration : `.agent-tasks/config.toml` (modèle : [`templates/config.toml`](templates/config.toml), incluant `permission`, `strict_allowed_tools` et `on_finish_cmd`).

### Exemple de prompt pour découper un plan (Opus / session interactive)

```text
Utilise le skill plan-tasks pour découper docs/mon-plan.md en micro-tâches
dans .agent-tasks/todo/ (ne modifie aucun fichier de code source).

- Lance d'abord `agent-runner models` pour vérifier les runners disponibles.
- Branche cible : `refactor/mon-chantier` depuis `main` (batch `mon-chantier`, merge_strategy `none`).
- Inclus l'extrait exact du plan et les numéros de lignes dans chaque fiche pour qu'elle soit autonome.
- Privilégie `move-lines` pour les déplacements de blocs importants et fixe un `verify_cmd` rapide ciblé sur le module modifié.
```

## Helpers déterministes

Les helpers (`helpers/`, stdlib Python uniquement, installés dans `~/.local/bin`) remplacent des lectures ou réécritures coûteuses en tokens par des opérations atomiques et déterministes autorisables via `allowed_tools` :

- **`file-outline <fichier> [--max-depth N]`** : extrait la carte structurelle et les plages de lignes exactes (Python, Rust, Kotlin, TS/JS, Go, Java, C/C++, Terraform/HCL, Ansible/YAML, Markdown, TOML, JSON, SQL, Makefile, Shell). Évite aux modèles (et à `plan-tasks`) de lire des fichiers entiers de 800+ lignes et élimine les hallucinations de numéros de lignes.
- **`move-lines [--after N] <src> <début> <fin> <dst>`** : déplace un bloc de code d'un fichier à un autre sans le régénérer, ce qui économise les tokens de sortie, préserve le formatage exact et évite les erreurs de recopie sur les modèles légers.
- **`replace-block [--expected-lines N] [--anchor TEXT] <fichier> <début> <fin> [remplacement]`** : remplace chirurgicalement une plage de lignes (via fichier ou `stdin`) avec écriture atomique et vérification de cohérence (`--expected-lines`, `--anchor`), évitant la réécriture complète d'un fichier ou les échecs d'indentation.

## Organisation

| Chemin | Rôle |
| :--- | :--- |
| `bin/agent-runner` | CLI (`init`, `run`, `models`, `status`, `report`, `retry`, `clean`) |
| `adapters/` | adaptateurs `claude` (flux `stream-json`, plafond `max_turns` appliqué par le runner) et `agy` |
| `helpers/move-lines` | déplace une plage de lignes entre fichiers sans régénérer le code |
| `helpers/file-outline` | carte structurelle (classes, fonctions, méthodes) et numéros de lignes exacts |
| `helpers/replace-block` | remplacement chirurgical d'une plage de lignes (avec garde-fous `--expected-lines` / `--anchor`) |
| `skills/` | skills `plan-tasks`, `triage-failed` et `review-batch`, communs à Claude Code et `agy` |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Les tests utilisent des dépôts Git temporaires et un faux `claude` placé dans le `PATH`.

## Licence

[MIT](LICENSE)

## Limites connues

- `codex` (optionnel, sans effet s'il est absent) : `codex exec --json` ; ni liste blanche d'outils ni budget, `max_turns` approché ; chaîne `[escalation].codex` à adapter aux modèles proposés. Après installation de Codex, relancer `./install.sh` pour installer la skill dans `~/.codex/skills`.
- `agy` : ni liste blanche d'outils, ni `max_turns`, ni budget ne sont transmis (non exposés par `agy --help`) ; le format JSON de sortie étant non documenté, l'analyse des métriques est tolérante. En mode headless (`--print`), `permission = "bypass"` (ou des règles `permissions.allow` dans `settings.json`) est requis pour autoriser les commandes shell (`git commit`, `verify_cmd`) ; en cas de refus détecté, l'escalade est stoppée immédiatement. Les alias de la chaîne `[escalation].agy` sont à adapter (`agy models`).
- `--with-agent-rules` écrit dans `CLAUDE.md` (créé si absent) et `AGENTS.md` s'il existe.
