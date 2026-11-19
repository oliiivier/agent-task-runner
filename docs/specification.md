# Spécifications techniques : `agent-task-runner`

## 1. Objectif et philosophie

`agent-task-runner` est un orchestrateur externe léger permettant d'exécuter une file de tâches de développement de manière autonome, séquentielle et reproductible via des CLI d'agents IA (`claude -p`, `agy`, etc.).

Il repose sur trois principes fondamentaux :

1. **Découplage planification / exécution :** Un modèle de raisonnement avancé (en session interactive) découpe un chantier complexe en micro-tâches atomiques stockées dans un répertoire `todo/`. Un runner non interactif dépile ensuite ces tâches une par une en contexte vierge avec le modèle le plus économique adapté à chaque étape.
2. **Contrôle d'intégrité externe :** L'agent modifie le code et crée le commit, mais **seul le script orchestrateur** valide les post-conditions (code retour, propreté de l'arbre Git, avancée de `HEAD`, exécution d'une commande de vérification externe), déplace la tâche vers `done/` ou `failed/`, et gère le cycle de vie des branches Git.
3. **Réutilisabilité multi-projets :** Développé dans un dépôt autonome et installé globalement dans le `PATH`, il initialise et pilote n'importe quel dépôt Git existant de manière non intrusive.

---

## 2. Architecture du dépôt outil (`agent-task-runner`)

```text
agent-task-runner/
├── bin/
│   └── agent-runner              # Point d'entrée exécutable (Python 3.11+ stdlib uniquement)
├── adapters/
│   ├── __init__.py
│   ├── base.py                   # Interface commune des adaptateurs CLI
│   ├── claude.py                 # Adaptateur pour `claude -p`
│   └── agy.py                    # Adaptateur pour `agy` (mode non-interactif)
├── skills/
│   ├── claude/
│   │   └── plan-tasks/
│   │       └── SKILL.md          # Skill Claude Code de génération des tâches
│   └── agy/
│       └── plan-tasks.md         # Règle / workflow équivalent pour agy
├── templates/
│   ├── config.toml               # Modèle de configuration par projet
│   └── example-task.md           # Exemple de tâche commenté
├── schemas/
│   └── task.schema.json          # Schéma JSON de validation du frontmatter YAML
├── tests/
│   └── test_runner.py            # Tests d'intégration sur dépôts Git temporaires
├── README.md
└── install.sh                    # Installation globale via symlinks (~/.local/bin, ~/.claude/skills)
```

### Contraintes techniques de l'implémentation

* **Langage :** Python 3.11+ utilisant **exclusivement la bibliothèque standard** (`argparse`, `subprocess`, `json`, `tomllib`, `pathlib`, `shutil`, `re`, `time`, `dataclasses`) afin de garantir une installation zéro-dépendance sur Linux/macOS.
* **Extensibilité :** Chaque outil IA est encapsulé dans un adaptateur (`adapters/`) chargé de traduire la configuration agnostique d'une tâche (modèle, prompt, outils autorisés, budget) en arguments CLI spécifiques et de normaliser les métriques de sortie (tokens, coût, statut d'erreur).

---

## 3. Structure et initialisation d'un projet cible (`agent-runner init`)

### 3.1. Arborescence créée dans le dépôt projet

Dans chaque dépôt projet utilisant l'outil, un dossier dédié (non suivi par Git) héberge la configuration locale et l'état de la file :

```text
<projet>/
└── .agent-tasks/                 # Ignoré par Git (.git/info/exclude par défaut)
    ├── config.toml               # Valeurs par défaut propres au projet (stack, verify_cmd, etc.)
    ├── todo/                     # Tâches en attente, triées par ordre alphanumérique
    │   ├── 00-example.md.disabled
    │   ├── 01-extract-s3bar.md
    │   └── 02-extract-banners.md
    ├── done/                     # Tâches validées et commitées
    ├── failed/                   # Tâches ayant échoué (avec diagnostic annexé)
    └── logs/                     # Sorties détaillées par exécution
        ├── 01-extract-s3bar.log  # Sortie texte / stderr
        ├── 01-extract-s3bar.json # Réponse brute structurée du CLI
        └── usage.tsv             # Journal cumulatif des tokens, coûts et durées
```

### 3.2. Spécification de la commande `agent-runner init`

La commande `agent-runner init [--gitignore] [--with-agent-rules]` adapte un dépôt Git existant de manière **idempotente** (relançable sans écraser les tâches en cours ni dupliquer les configurations) :

1. **Vérification Git :** S'assure que le répertoire courant se trouve dans un dépôt Git valide (`git rev-parse --git-dir`).
2. **Création de l'arborescence :** Crée `.agent-tasks/{todo,done,failed,logs}` ainsi qu'un fichier modèle `.agent-tasks/todo/00-example.md.disabled`.
3. **Isolation Git non intrusive :**
   * *Par défaut :* Ajoute `.agent-tasks/` dans `.git/info/exclude` (s'il n'y figure pas déjà). Cela garde l'état des tâches strictement local sans modifier `.gitignore` ni salir l'historique Git d'un dépôt partagé.
   * *Option `--gitignore` :* Ajoute `.agent-tasks/` dans `.gitignore` à la racine du dépôt.
4. **Détection automatique de la stack et génération de `.agent-tasks/config.toml` :**
   * Détecte la branche principale (`main` ou `master` via `git symbolic-ref` / `git branch`).
   * Inspecte les marqueurs à la racine du projet pour pré-remplir `default_verify_cmd` et `default_allowed_tools` :
     * `Cargo.toml` $\rightarrow$ `cargo test`
     * `gradlew` / `build.gradle.kts` $\rightarrow$ `./gradlew testDebugUnitTest` (ou `tools/check-android.sh` si présent)
     * `pyproject.toml` $\rightarrow$ `pytest`
     * `package.json` $\rightarrow$ `npm test`
   * Écrit `.agent-tasks/config.toml` uniquement s'il n'existe pas déjà.
5. **Injection optionnelle dans les instructions projet (`--with-agent-rules`) :**
   * Si `CLAUDE.md` (ou le fichier de règles `agy`) existe à la racine, ajoute un bloc délimité par des marqueurs idempotents (`<!-- BEGIN agent-task-runner -->` ... `<!-- END agent-task-runner -->`) rappelant aux agents de ne jamais lire ni modifier `.agent-tasks/` et de respecter les commits atomiques.

> **Sécurité Git au runtime :** Avant chaque exécution de `run`, `agent-runner` vérifie via `git check-ignore -q .agent-tasks` que le dossier de tâches est bien ignoré par Git, bloquant le démarrage dans le cas contraire.

---

## 4. Spécification du format d'une tâche (`todo/NN-slug.md`)

Chaque tâche est un **fichier Markdown unique** composé :

1. D'un **en-tête YAML (*frontmatter*)** délimité par `---` contenant les métadonnées d'orchestration (surchargeant les défauts de `.agent-tasks/config.toml`).
2. D'un **corps Markdown** contenant les instructions métier transmises comme prompt à l'agent.

### 4.1. Contrat du Frontmatter

| Champ | Type | Obligatoire | Défaut (`config.toml` ou global) | Description |
| :--- | :--- | :---: | :--- | :--- |
| `id` | `string` | Oui | — | Identifiant unique de la tâche (ex. `03-extract-banners`). |
| `batch` | `string` | Non | `"default"` | Identifiant du lot logique regroupant plusieurs tâches dépendantes. |
| `branch` | `string` | Oui | — | Branche Git cible sur laquelle le commit sera créé. |
| `base_branch` | `string` | Non | `"main"` | Branche source utilisée si `branch` n'existe pas encore. |
| `merge_strategy` | `enum` | Non | `"none"` | Stratégie appliquée une fois **toutes** les tâches du `batch` réussies : `none`, `auto_ff` (fast-forward uniquement), `auto_no_ff` (merge commit). |
| `runner` | `enum` | Non | `"claude"` | Exécutable cible : `claude` ou `agy`. |
| `model` | `string` | Non | `"haiku"` | Modèle recommandé (`haiku`, `sonnet`, `opus`, ou identifiant supporté par `agy`). |
| `verify_cmd` | `string` | Non | `""` | Commande shell exécutée directement par `agent-runner` **après** l'agent pour valider l'état du dépôt. |
| `require_commit` | `bool` | Non | `true` | Exige que `HEAD` ait avancé d'au moins un commit (`false` pour une tâche d'audit ou de baseline). |
| `allowed_tools` | `list[str]`| Non | *profil défaut* | Liste blanche des outils/commandes autorisés en mode non interactif. |
| `timeout_sec` | `int` | Non | `900` | Délai maximal d'exécution de l'agent en secondes. |
| `max_budget_usd` | `float` | Non | `null` | Plafond financier optionnel transmis au CLI (`--max-budget-usd`). |

### 4.2. Exemple de fichier de tâche

```markdown
---
id: 03-extract-banners
batch: split-reader
branch: refactor/split-reader-screen
base_branch: main
merge_strategy: auto_no_ff
runner: claude
model: haiku
require_commit: true
verify_cmd: "tools/check-android.sh :feature:reader:compileDebugKotlin :feature:reader:testDebugUnitTest"
allowed_tools:
  - "Bash(git:*)"
  - "Bash(tools/check-android.sh:*)"
timeout_sec: 600
---

## Objectif
Extraire `BookRepairBanner`, `BookRepairSection` (en `internal`) et `repairIssueLabel` depuis `ReaderScreen.kt` vers `ReaderBanners.kt`.

## Contexte et règles
- Lis `CLAUDE.md` avant de commencer.
- Déplace le code à l'identique avec ses commentaires KDoc et supprime les imports inutilisés.
- Produis un commit unique en anglais au format conventionnel (`refactor(reader): extract repair banners`).
- Ne modifie aucun fichier dans `.agent-tasks/`.
```

---

## 5. Machine à états et cycle d'exécution (`agent-runner`)

### 5.1. Séquence d'exécution d'une tâche

Pour chaque fichier `todo/*.md` trié par ordre lexicographique (en ignorant les fichiers `.disabled`, filtré optionnellement par `--batch`) :

1. **Pré-conditions (Fail-Fast) :**
   * Vérifier que `.agent-tasks` est ignoré par Git (`git check-ignore -q .agent-tasks`).
   * Vérifier que l'arbre de travail Git est propre (`git status --porcelain --untracked-files=no` vide), sauf si l'option `--resume` est active.
   * Basculer sur `branch` (`git checkout <branch>`), ou la créer depuis `base_branch` (`git checkout -b <branch> <base_branch>`) si elle n'existe pas.

2. **Préparation du prompt et reprise :**
   * Enregistrer `HEAD_BEFORE = $(git rev-parse HEAD)`.
   * Si `--resume` est actif et que l'arbre contient des modifications partielles sur `branch`, préfixer le prompt avec l'état `git status --short` pour demander à l'agent d'inspecter, compléter et commiter le travail en cours plutôt que de tout recommencer.

3. **Invocation de l'adaptateur CLI :**
   * Exécuter l'agent en session vierge (`--no-session-persistence`, sortie structurée JSON).
   * Enregistrer les logs bruts dans `.agent-tasks/logs/<id>.log` et `.json`.
   * Extraire et ajouter une ligne dans `.agent-tasks/logs/usage.tsv` :
     `timestamp | batch | id | runner | model | tokens_in | cache_write | cache_read | tokens_out | cost_usd | duration_sec | status`

4. **Validation des post-conditions (par le runner, indépendamment de l'agent) :**
   * **C1 — Exécution :** Le processus CLI s'est terminé sans timeout, avec un code retour `0` et sans indicateur d'erreur (`is_error == false`).
   * **C2 — Propreté Git :** Aucun fichier suivi modifié non commité ne subsiste (`git status --porcelain --untracked-files=no` vide).
   * **C3 — Progression :** Si `require_commit: true`, vérifier que `HEAD_AFTER != HEAD_BEFORE`.
   * **C4 — Vérification métier :** Si `verify_cmd` est renseigné, exécuter la commande shell depuis la racine du dépôt et vérifier que son code retour est `0`.

5. **Transition d'état :**
   * **Succès (`C1` à `C4` valides) :** Déplacer `.agent-tasks/todo/<fichier>.md` $\rightarrow$ `.agent-tasks/done/<fichier>.md`.
   * **Échec :**
     * *Mode strict (défaut) :* Laisser le fichier dans `todo/` (et conserver l'état partiel pour permettre un `--resume` après inspection) et interrompre immédiatement l'exécution.
     * *Mode `--continue-on-error` :* Réinitialiser l'arbre de travail (`git reset --hard $HEAD_BEFORE` et `git clean -fd`), annexer le rapport d'échec à la fin du fichier Markdown, déplacer vers `.agent-tasks/failed/<fichier>.md`, et passer à la tâche suivante.

### 5.2. Finalisation d'un lot (`batch`)

À la fin du traitement de la file, pour chaque `batch` ayant une `merge_strategy` différente de `none` :

1. Vérifier qu'il ne reste **aucune** tâche de ce `batch` dans `todo/` ni dans `failed/`.
2. Basculer sur `base_branch` (`git checkout <base_branch>`).
3. Exécuter le merge selon la stratégie définie :
   * `auto_ff` : `git merge --ff-only <branch>`
   * `auto_no_ff` : `git merge --no-ff <branch> -m "Merge batch '<batch>' from <branch>"`
4. Si le merge réussit, supprimer la branche temporaire (`git branch -d <branch>`). En cas de conflit de merge, avorter (`git merge --abort`), laisser `branch` intacte et signaler une erreur explicite.

---

## 6. Interface en ligne de commande (CLI)

```text
Usage:
  agent-runner init [options]           Initialise .agent-tasks/ dans le dépôt courant
    --gitignore                         Ajoute .agent-tasks/ à .gitignore au lieu de .git/info/exclude
    --with-agent-rules                  Ajoute les consignes d'isolation dans CLAUDE.md / règles agy
  agent-runner run [options]            Exécute les tâches présentes dans .agent-tasks/todo/
    --batch <nom>                       Ne traite que les tâches appartenant au lot <nom>
    --dry-run                           Affiche le plan d'exécution sans invoquer les agents ni toucher à Git
    --resume                            Reprend une tâche interrompue en conservant les modifications non commitées
    --continue-on-error                 En cas d'échec, rollback la tâche vers failed/ et continue le lot
    --model <modèle>                    Force un modèle spécifique pour toutes les tâches exécutées
  agent-runner status                   Affiche l'avancement (todo/done/failed) et les coûts cumulés
  agent-runner retry <id>               Déplace une tâche de failed/ vers todo/ (en retirant le rapport d'erreur)
  agent-runner clean [--all]            Supprime logs/ (et done/ si --all est précisé)
```

---

## 7. Spécification du Skill de génération (`plan-tasks`)

Le skill `plan-tasks` (déployé globalement dans `~/.claude/skills/plan-tasks/SKILL.md` et dans la configuration globale `agy`) permet de transformer un cahier des charges ou un plan d'architecture en fichiers `.agent-tasks/todo/NN-slug.md` prêts à être exécutés.

### Directives comportementales du Skill

1. **Séparation des rôles :**
   * Lorsque l'utilisateur demande de planifier ou préparer des tâches autonomes, **ne modifie aucun fichier de code source**. Génère uniquement les fiches de tâches dans `.agent-tasks/todo/`.
   * Si `.agent-tasks/` n'existe pas encore dans le projet, exécute `agent-runner init` avant de créer les fiches.
2. **Règle d'atomicité (granulométrie cible : 10 à 20 tours d'agent) :**
   * Découpe le chantier en étapes unitaires compilables et testables individuellement.
   * Préfixe chaque nom de fichier par un numéro d'ordre sur deux chiffres (`01-`, `02-`, …) reflétant l'ordre strict des dépendances.
3. **Routage modèle et outil (optimisation coût / capacité) :**
   * Attribue `model: haiku` (ou équivalent rapide) aux tâches mécaniques et déterministes : extraction de composants sans changement de logique, renommages, ajout de tests unitaires simples, migrations répétitives.
   * Attribue `model: sonnet` (ou modèle avancé) aux tâches nécessitant une compréhension architecturale : refactoring avec changement d'état/couplage, résolution de conflits de types complexes, passe finale d'intégration.
4. **Gestion des branches et stratégies de merge :**
   * **Tâches séquentielles / dépendantes :** Utiliser le même `batch` et la même `branch` pour tout le lot. Fixer `merge_strategy: none` par défaut (pour validation humaine finale) ou `auto_no_ff` uniquement si la dernière tâche du lot exécute la suite de tests complète du projet.
   * **Tâches indépendantes :** Attribuer un `batch` et une `branch` distincts par tâche.
5. **Validation ciblée (`verify_cmd`) :**
   * Chaque tâche produisant du code doit définir un `verify_cmd` exécutant la vérification la plus rapide et ciblée possible (ex. compilation du seul module impacté) afin de limiter la durée et le coût de chaque boucle, en réservant les suites de tests complètes à la tâche finale du lot.
