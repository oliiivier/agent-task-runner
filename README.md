# agent-task-runner

Orchestrateur externe léger qui exécute une file de tâches de développement de manière autonome, séquentielle et reproductible via des CLI d'agents IA (`claude -p`, `agy`, `codex exec`).

Chaque tâche démarre au juste niveau (via le routage dynamique par palier `N1`/`N2`/`N3` et par quotas, ou sur le modèle le moins cher de la chaîne d'escalade) et n'escalade vers le modèle supérieur qu'en cas d'échec, en reprenant le travail partiel. Seul le runner valide les post-conditions (code retour, arbre propre, `HEAD` avancé, `verify_cmd`), journalise les métriques FinOps et déplace les tâches vers `done/` ou `failed/`.

Spécifications complètes : [`docs/specification.md`](docs/specification.md).

## Installation

Python 3.11+ (bibliothèque standard uniquement) et Git.

```bash
./install.sh              # symlinks : ~/.local/bin/{agent-runner,move-lines,file-outline,replace-block}
                          # skills dans ~/.claude/skills, ~/.gemini/config/skills (et ~/.codex/skills si Codex est présent)
./install.sh --uninstall
```

## Utilisation

```bash
cd mon-projet
agent-runner init [--gitignore] [--with-agent-rules] [--update-config]   # crée ou complète .agent-tasks/
agent-runner models [runner]                            # runners disponibles, catalogues et quotas temps réel
# générer les tâches avec le skill plan-tasks (interactif) ou en headless (un ou plusieurs documents) :
agent-runner create-plan docs/cartographie.md docs/plan.md [--batch X] [--branch feat/x] [--base-branch main] [--runner R] [--model M] [--effort E]
agent-runner run --dry-run                              # plan de routage, modèles résolus et chaînes d'escalade
agent-runner run [--batch X] [--once | --limit N] [--resume] [--continue-on-error] [--model M] [--no-escalate] [--avoid-runner R]
agent-runner status
agent-runner report [--batch X] [--last N] [--json]
agent-runner logs [<id>] [--batch X] [--file PATH] [-f|--stream] [--verify] [--raw]
agent-runner retry <id>                                 # ou --all
agent-runner merge [--batch X | --all] [--strategy no-ff|ff|squash] [--no-delete] [--force] [--dry-run]
agent-runner clean [--all]
```

### Cycle de vie type d'un chantier

1. **Planification (`create-plan` ou skill `plan-tasks`)** : produit des fiches autonomes `.agent-tasks/todo/NN-slug.md` (frontmatter YAML + corps autonome). En mode `create-plan`, si les documents sources contiennent des ambiguïtés bloquantes, l'agent s'arrête sans créer de fiches (code retour `2`), affiche ses questions et génère un prompt prêt à exécuter en session interactive ; un garde-fou Git annule aussi toute modification involontaire hors `.agent-tasks/`.
2. **Exécution (`run`)** : dépile les tâches dans l'ordre alphanumérique, gère la création/bascule de branche, l'escalade et les replis entre runners, puis affiche un bilan FinOps (même en cas d'interruption `Ctrl-C`).
3. **Reprise (`triage-failed` + `retry`)** : le skill `triage-failed` analyse `.agent-tasks/failed/` et les logs `.verify.log` pour réparer ou scinder les fiches avant `agent-runner retry`.
4. **Audit et fusion (`review-batch` + `merge`)** : si les fiches utilisent `merge_strategy: none` (recommandé pour un lot multi-tâches), le skill `review-batch` audite le diff global du lot avant `agent-runner merge` (les stratégies `auto_ff` / `auto_no_ff` fusionnent automatiquement dès la dernière tâche réussie).

### Configuration et routage

- **Hiérarchie de configuration** : une configuration globale optionnelle `~/.config/agent-task-runner/config.toml` (ou `$XDG_CONFIG_HOME/agent-task-runner/config.toml`) est fusionnée avec la configuration projet `.agent-tasks/config.toml` (modèle : [`templates/config.toml`](templates/config.toml)). `agent-runner init --update-config` ajoute les nouvelles sections `[routing*]` à un `config.toml` existant sans écraser vos réglages.
- **Routage par niveau et quotas** :
  - `level: N1 | N2 | N3` dans une fiche (sans `runner:` épinglé) résout automatiquement le runner via `[routing.level_runner]` (`N1`/`N2 → agy`, `N3 → claude` par défaut), le modèle de départ, le plafond et l'effort par défaut (`low`, `medium`, `high`).
  - `prefer_runner` exprime une préférence souple (autorisant la bascule automatique en cas de quota ou d'indisponibilité), contrairement à `runner` qui épingle strictement.
  - `[routing.quota_threshold]` (défaut `80 %`), `[routing.model_quota_threshold]` (ex. `argon = 60.0` pour préserver `gemini-4-argon`) et `[routing].reset_grace_min` (relâchement si le reset est imminent et le quota `< 95 %`) pilotent la bascule automatique ; `allow_premium: false` interdit l'escalade vers les modèles premium/rares (`argon`, `neon`, `opus`, `fable`).
- **Garde-fous et intégrations** : `permission` (`"accept"` généré par `init` et par défaut interne ; `"bypass"` dans [`templates/config.toml`](templates/config.toml)), `strict_allowed_tools`, `fallback_runner` (`none` pour interdire le repli), `[exclude_models]` (par défaut `agy = ["claude-*"]`), `[fallback_map]`, `[extra_args]` et `on_finish_cmd`.
- **Schéma des fiches** : [`templates/example-task.md`](templates/example-task.md) et [`schemas/task.schema.json`](schemas/task.schema.json).

Pour brancher un outil de ticketing ou de notification externe (Jira, Linear, Slack…) via `on_finish_cmd` et `agent-runner report --json`, voir [`docs/ticketing-integration.md`](docs/ticketing-integration.md). Pour l'interprétation des métriques d'économie de coûts, de paliers `N1`/`N2`/`N3` et de cache dans `report`, voir [`docs/cost-savings-example.md`](docs/cost-savings-example.md).

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
| `bin/agent-runner` | point d'entrée (≈ 15 lignes) |
| `agent_runner/` | package (`cli.py`, `commands/`, `routing.py`, `execute.py`, `adapters/`, `logs.py`, …) |
| `agent_runner/adapters/` | adaptateurs `claude` (`stream-json`, `max_turns` et `allowed_tools`), `agy`, `codex` |
| `helpers/move-lines` | déplace une plage de lignes entre fichiers sans régénérer le code |
| `helpers/file-outline` | carte structurelle (classes, fonctions, méthodes) et numéros de lignes exacts |
| `helpers/replace-block` | remplacement chirurgical d'une plage de lignes (avec garde-fous `--expected-lines` / `--anchor`) |
| `skills/` | skills `plan-tasks`, `triage-failed` et `review-batch`, communs à Claude Code, `agy` et Codex |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Les tests (`tests/test_integration.py`, `tests/test_helpers.py`, `tests/test_file_outline.py`) utilisent des dépôts Git temporaires et un faux `claude` placé dans le `PATH`.

## Licence

[MIT](LICENSE)

## Limites connues

- `agy` : ni liste blanche d'outils (`allowed_tools`), ni `max_turns`, ni budget ne sont transmis (non exposés par `agy --help`) ; le format JSON de sortie étant non documenté, l'analyse des métriques est tolérante et le coût facturé rapporté vaut `$0.00` (compensé par une estimation API dans `report`). En mode headless (`--print`), `permission = "bypass"` (ou des règles `permissions.allow` dans `settings.json`) est requis pour autoriser les commandes shell (`git commit`, `verify_cmd`) ; en cas de refus détecté, l'escalade est stoppée immédiatement. `[escalation].agy` attend des **familles sans suffixe d'effort** : le runner compose `<famille>-<effort>` (ou l'effort disponible le plus proche) d'après `agy models`.
- `codex` (optionnel, sans effet s'il est absent) : `codex exec --json` ; ni liste blanche d'outils ni budget, `max_turns` approché ; avec `permission = "accept"`, `--sandbox workspace-write` monte `.git/` en lecture seule et bloque `git commit` (`permission = "bypass"` requis). La chaîne par défaut (`gpt-5.6-luna -> gpt-5.6-terra -> gpt-6-luna`) cible un compte ChatGPT ; à adapter dans `[escalation].codex` pour une clé API. Après installation de Codex, relancer `./install.sh` pour installer les skills dans `~/.codex/skills`.
- `--with-agent-rules` écrit dans `CLAUDE.md` (créé si absent) et `AGENTS.md` s'il existe.
