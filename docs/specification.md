# Spécifications techniques : `agent-task-runner`

## 1. Objectif et philosophie

`agent-task-runner` est un orchestrateur externe léger permettant d'exécuter une file de tâches de développement de manière autonome, séquentielle et reproductible via des CLI d'agents IA (`claude -p`, `agy`, etc.).

Il repose sur quatre principes fondamentaux :

1. **Découplage planification / exécution :** Un modèle de raisonnement avancé (en session interactive) découpe un chantier complexe en micro-tâches atomiques stockées dans un répertoire `todo/`. Un runner non interactif dépile ensuite ces tâches une par une en contexte vierge.
2. **Optimisation FinOps par escalade hiérarchique :** Chaque tâche démarre par défaut sur le modèle le plus rapide et économique du fournisseur (`haiku` sur Claude, équivalent rapide sur `agy`). Le runner n'escalade vers le modèle supérieur de la chaîne (`haiku` $\rightarrow$ `sonnet` $\rightarrow$ `opus` $\rightarrow$ `fable`) **qu'en cas d'échec**, en reprenant le travail partiel déjà produit.
3. **Contrôle d'intégrité externe :** L'agent modifie le code et crée le commit, mais **seul le script orchestrateur** valide les post-conditions (code retour, propreté de l'arbre Git, avancée de `HEAD`, exécution d'une commande de vérification externe), déplace la tâche vers `done/` ou `failed/`, et gère le cycle de vie des branches Git.
4. **Réutilisabilité multi-projets :** Développé dans un dépôt autonome et installé globalement dans le `PATH`, il initialise et pilote n'importe quel dépôt Git existant de manière non intrusive.

---

## 2. Architecture du dépôt outil (`agent-task-runner`)

```text
agent-task-runner/
├── bin/
│   └── agent-runner              # Point d'entrée exécutable (Python 3.11+ stdlib uniquement)
├── adapters/
│   ├── __init__.py
│   ├── base.py                   # Interface commune des adaptateurs CLI et chaînes d'escalade
│   ├── codex.py                  # Adaptateur optionnel pour `codex exec`
│   ├── claude.py                 # Adaptateur pour `claude -p` (chaîne : haiku -> sonnet -> opus -> fable)
│   └── agy.py                    # Adaptateur pour `agy` (chaîne configurable équivalente)
├── helpers/
│   └── move-lines                # Utilitaire atomique de déplacement de plages de lignes entre fichiers
├── skills/
│   └── plan-tasks/
│       └── SKILL.md              # Skill de génération des tâches, commun à Claude Code et agy
├── templates/
│   ├── config.toml               # Modèle de configuration par projet
│   └── example-task.md           # Exemple de tâche commenté
├── schemas/
│   └── task.schema.json          # Schéma JSON de validation du frontmatter YAML
├── tests/
│   └── test_runner.py            # Tests d'intégration sur dépôts Git temporaires
├── README.md
└── install.sh                    # Installation globale via symlinks (~/.local/bin, ~/.claude/skills, ~/.gemini/config/skills)
```

### Contraintes techniques et utilitaires inclus

* **Langage :** Python 3.11+ utilisant **exclusivement la bibliothèque standard** (`argparse`, `subprocess`, `json`, `tomllib`, `pathlib`, `shutil`, `re`, `time`, `dataclasses`) afin de garantir une installation zéro-dépendance sur Linux/macOS.
* **Utilitaire `helpers/move-lines` :** Installé aux côtés de `agent-runner` (ex. `move-lines <src> <start_line> <end_line> <dst>`). Permet à l'agent de déplacer des blocs de code volumineux (300–600+ lignes) sans avoir à régénérer deux fois le code en tokens de sortie (`Write` + `Edit` de suppression), divisant par 2 à 3 le volume d'`output_tokens` sur les refactorings.

---

## 3. Structure et initialisation d'un projet cible (`agent-runner init`)

### 3.1. Arborescence créée dans le dépôt projet

Dans chaque dépôt projet utilisant l'outil, un dossier dédié (non suivi par Git) héberge la configuration locale et l'état de la file :

```text
<projet>/
└── .agent-tasks/                 # Ignoré par Git (.git/info/exclude par défaut)
    ├── config.toml               # Chaînes d'escalade, verify_cmd par défaut, profils d'outils
    ├── todo/                     # Tâches en attente, triées par ordre alphanumérique
    │   ├── 00-example.md.disabled
    │   ├── 01-extract-s3bar.md
    │   └── 02-extract-banners.md
    ├── done/                     # Tâches validées et commitées
    ├── failed/                   # Tâches ayant échoué après épuisement de l'escalade
    └── logs/                     # Sorties détaillées par exécution et par tentative
        ├── 01-extract-s3bar.try1-haiku.log
        ├── 01-extract-s3bar.try1-haiku.json
        └── usage.tsv             # Journal cumulatif (tokens, cache, coût, modèle, tentative)
```

### 3.2. Spécification de la commande `agent-runner init`

La commande `agent-runner init [--gitignore] [--with-agent-rules]` adapte un dépôt Git existant de manière **idempotente** :

1. **Vérification Git :** S'assure que le répertoire courant se trouve dans un dépôt Git valide (`git rev-parse --git-dir`).
2. **Création de l'arborescence :** Crée `.agent-tasks/{todo,done,failed,logs}` ainsi que `.agent-tasks/todo/00-example.md.disabled`.
3. **Isolation Git non intrusive :**
   * *Par défaut :* Ajoute `.agent-tasks/` dans `.git/info/exclude` (s'il n'y figure pas déjà).
   * *Option `--gitignore` :* Ajoute `.agent-tasks/` dans `.gitignore` à la racine du dépôt.
4. **Détection automatique de la stack et génération de `.agent-tasks/config.toml` :**
   * Détecte la branche principale (`main` ou `master`).
   * Inspecte les marqueurs à la racine (`Cargo.toml`, `gradlew`, `pyproject.toml`, `package.json`, `tools/check-*.sh`) pour pré-remplir `default_verify_cmd` et `default_allowed_tools` (incluant `Bash(move-lines:*)`).
   * Initialise les chaînes d'escalade par défaut par fournisseur (`[escalation]`).
   * Vérifie chaque runner (cf. §4.6) et fixe `default_runner` sur un runner **installé et connecté** (préférence : `claude`, puis `agy`, puis `codex`).
   * Si `config.toml` existe déjà, il n'est jamais régénéré : seule la ligne `default_runner` est réécrite, et uniquement si le runner configuré est devenu indisponible alors qu'un autre est utilisable (commentaires et reste du fichier préservés). Relancer `init` sans changement d'environnement laisse le fichier strictement identique ; si aucun runner n'est utilisable, rien n'est modifié et un avertissement est affiché.
5. **Injection optionnelle dans les instructions projet (`--with-agent-rules`) :**
   * Ajoute un bloc délimité par des marqueurs idempotents (`<!-- BEGIN agent-task-runner -->` ... `<!-- END agent-task-runner -->`) dans `CLAUDE.md` / règles `agy` rappelant de ne pas toucher à `.agent-tasks/` et de privilégier `move-lines` pour les déplacements de blocs importants.

---

## 4. Escalade hiérarchique des modèles (`[escalation]`)

### 4.1. Principe de fonctionnement

Chaque fournisseur (`runner`) dispose d'une **liste ordonnée de modèles** allant du moins coûteux au plus puissant (*top-gun*), configurée globalement et surchargeable dans `.agent-tasks/config.toml` :

```toml
[escalation]
claude = ["haiku", "sonnet", "opus", "fable"]
agy    = ["gemini-3.8-flash", "gemini-4-hydrogen", "gemini-4-argon", "gemini-4-neon"]  # familles, sans suffixe d'effort
```

Lorsqu'une tâche s'exécute :
1. Elle démarre avec son modèle initial `model` (par défaut le **premier maillon** de la chaîne du `runner`, ex. `haiku`).
2. Si la tentative échoue (erreur CLI, timeout, `max_turns` atteint, arbre sale ou échec de `verify_cmd`) **et** que `escalate` est activé (défaut : `true`) :
   * Le runner identifie le **modèle suivant** dans la liste ordonnée du fournisseur (jusqu'à `max_model` si spécifié).
   * Le travail partiel déjà écrit sur la branche est **conservé** (comme pour un `--resume`), et le rapport d'erreur de la tentative précédente (extrait des logs ou sortie de `verify_cmd`) est injecté en tête du prompt pour que le modèle supérieur corrige directement le blocage sans refaire le travail déjà accompli.
3. Si le modèle atteint le sommet de la chaîne (ou `max_model`) et échoue encore, la tâche est déclarée en échec définitif.

### 4.2. Contrat du Frontmatter d'une tâche (`todo/NN-slug.md`)

| Champ | Type | Obligatoire | Défaut (`config.toml` ou global) | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | `string` | Oui | — | Identifiant unique de la tâche (ex. `03-extract-banners`). |
| `batch` | `string` | Non | `"default"` | Identifiant du lot logique regroupant plusieurs tâches dépendantes. |
| `branch` | `string` | Oui | — | Branche Git cible sur laquelle le commit sera créé. |
| `base_branch` | `string` | Non | `"main"` | Branche source utilisée si `branch` doit être créée. |
| `merge_strategy` | `enum` | Non | `"none"` | Stratégie appliquée une fois **toutes** les tâches du `batch` réussies : `none`, `auto_ff`, `auto_no_ff`. |
| `runner` | `enum` | Non | `"claude"` | Exécutable cible : `claude` ou `agy`. |
| `fallback_runner` | `enum` | Non | auto | Runner de repli (≠ `runner`) utilisé si le nominal atteint son quota, est indisponible (cf. §4.6) ou est évité via `--avoid-runner` (cf. §4.4). Absent : l'autre runner si `auto_fallback = true` (défaut). `none` : aucun repli. |
| `model` | `string` | Non | *1er de la chaîne* | Modèle de départ (ex. `haiku`). |
| `effort` | `enum` | Non | *défaut du CLI* | Effort de raisonnement transmis via `--effort` : `low`, `medium`, `high`, `xhigh`, `max`. |
| `escalate` | `bool` | Non | `true` | Active la montée automatique dans la chaîne du fournisseur en cas d'échec. |
| `max_model` | `string` | Non | *dernier de la chaîne* | Plafond d'escalade autorisé pour cette tâche (ex. `sonnet` ou `opus`). |
| `verify_cmd` | `string` | Non | `""` | Commande shell exécutée par `agent-runner` **après** l'agent pour valider le dépôt. |
| `require_commit` | `bool` | Non | `true` | Exige que `HEAD` ait avancé d'au moins un commit (`false` pour une baseline/audit). |
| `allowed_tools` | `list[str]`| Non | *profil défaut* | Liste blanche des outils autorisés en mode non interactif. |
| `max_turns` | `int` | Non | `35` | Plafond de tours d'agent (évite les boucles de compilation coûteuses type 40+ tours ; déclenche l'escalade si dépassé). |
| `timeout_sec` | `int` | Non | `900` | Délai maximal d'exécution d'une tentative en secondes. |
| `max_budget_usd`| `float` | Non | `null` | Plafond financier optionnel transmis au CLI (`--max-budget-usd`). |

### 4.3. Exemple de fichier de tâche

```markdown

---
id: 10-extract-remotesync
batch: split-reader
branch: refactor/split-reader-screen
base_branch: main
merge_strategy: auto_no_ff
runner: claude
model: haiku
escalate: true
max_model: sonnet
max_turns: 30
require_commit: true
verify_cmd: "tools/check-android.sh :feature:reader:compileDebugKotlin :feature:reader:testDebugUnitTest"
allowed_tools:
  - "Bash(git:*)"
  - "Bash(move-lines:*)"
  - "Bash(tools/check-android.sh:*)"
---

## Objectif
Extraire `RemoteSyncBanner` depuis `ReaderScreen.kt` (lignes 1420 à 1790) vers `ReaderBanners.kt`.

## Contexte extrait du plan (autonome — inutile de relire tout le plan)
- Déplacer `RemoteSyncBanner` et ses fonctions privées associées avec leurs commentaires KDoc.
- Passer `syncStateLabel` en `internal` dans `ReaderBanners.kt`.
- Utiliser `move-lines` pour le déplacement brut des lignes avant d'ajuster les imports.
- Produire un commit unique en anglais (`refactor(reader): extract RemoteSyncBanner`).
```


### 4.4. Routage dynamique entre runners (`fallback_runner`)

* **Quota / indisponibilité :** une tentative dont l'erreur CLI correspond à une limite du fournisseur (limite d'usage, `rate limit`, `quota`, `RESOURCE_EXHAUSTED`, surcharge 429/529) est journalisée `quota`. Elle **n'entraîne pas d'escalade** chez ce fournisseur : le runner est marqué indisponible pour le reste du `run`, et la tâche bascule sur `fallback_runner` en conservant le travail partiel.
* **Transposition des modèles :** `model`, `max_model`, `--model` et le rang atteint sont transposés vers la chaîne du runner de repli selon `[fallback_map.<destination>]` (défaut claude→agy : `haiku→gemini-3.8-flash`, `sonnet→gemini-4-argon`, `opus`/`fable→gemini-4-neon` ; agy→claude : `gemini-3.8-flash→haiku`, `hydrogen`/`argon→sonnet`, `neon→opus`), à défaut au rang relatif équivalent arrondi vers le haut.
* **Choix au lancement :** `agent-runner run --avoid-runner claude` fait partir d'emblée sur leur `fallback_runner` les tâches dont le runner nominal est `claude` (quota presque plein) sans modifier les fiches ; un runner indisponible (cf. §4.6) est traité de la même façon.
* **Repli automatique :** une fiche sans `fallback_runner` se replie sur l'autre runner, avec la même transposition des modèles, tant que `auto_fallback = true` (défaut de la configuration). `fallback_runner: none` interdit tout repli pour cette fiche (garde-fous propres à claude : `allowed_tools`, `max_turns`, budget) ; `auto_fallback = false` rétablit globalement « pas de champ = pas de repli ». `--dry-run` signale ces replis par `repli auto`. Les replis vers un runner qui n'applique pas `allowed_tools` (agy, codex) sont signalés par `[sans restriction d'outils]` ; `strict_allowed_tools = true` (défaut `false`) supprime ces replis automatiques et rejette un `fallback_runner` explicite de ce type.

### 4.5. Catalogue de modèles et effort (`agy models`)

* Chez `agy`, l'effort fait partie de l'identifiant (`gemini-4-neon-high`). La chaîne `[escalation].agy` liste des **familles** ; à chaque tentative, le runner compose `<famille>-<effort>` à partir du champ `effort` (défaut `medium`) en choisissant l'effort disponible le plus proche (à égalité, le plus élevé). Un identifiant complet dans la chaîne ou la fiche est utilisé tel quel. `--effort` n'est transmis qu'à `claude`.
* Au début de chaque `run` (y compris `--dry-run`), `agy models` est interrogé une fois. Les maillons absents du catalogue sont retirés de la chaîne avec un avertissement ; une tâche dont `model`/`max_model` est absent est rejetée avant toute exécution. Si la commande échoue, agy est considéré comme non connecté et donc indisponible (cf. §4.6).
* `[exclude_models]` (motifs fnmatch, défaut `agy = ["claude-*"]`) retire du catalogue les modèles à ne jamais utiliser via un CLI : les modèles Claude exposés par agy ont un quota très réduit.
* `agent-runner models [runner]` affiche d'abord `runners disponibles : …` (et le motif de chaque runner indisponible), puis la chaîne configurée, les exclusions et le catalogue disponible (familles et efforts).

### 4.5 bis. Runner optionnel `codex`

`codex exec --json` (OpenAI Codex CLI) est pris en charge en option : absent ou non connecté, il est simplement indisponible et n'a aucun effet (il n'apparaît que dans `agent-runner models`). Chaîne par défaut `gpt-5.4-mini → gpt-5.4 → gpt-5.5`, à adapter dans `[escalation].codex`. `effort` est transmis via `-c model_reasoning_effort=…` (`max` → `xhigh`), `permission = "accept"` correspond à `--sandbox workspace-write`. `allowed_tools` et le budget ne sont pas transmis ; `max_turns` est approché en comptant les actions de l'agent (commandes, modifications de fichiers, outils). Connexion vérifiée par `codex login status` (ou `CODEX_API_KEY`/`OPENAI_API_KEY`).

### 4.6. Disponibilité des runners

Un runner est **disponible** s'il remplit trois conditions, vérifiées par `init`, `models` et au début de chaque `run` (y compris `--dry-run`) :

1. **Autorisé** par la clé `runners` de la configuration (défaut : tous ; ex. `runners = ["claude"]` pour écarter `agy` installé mais non souhaité ; l'ordre de la liste est aussi la préférence du repli automatique) ;
2. **Installé** : exécutable présent dans le `PATH` ;
3. **Connecté** : `claude auth status` ne renvoie pas `loggedIn: false` (une variable `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` suffit ; une sortie illisible n'est pas bloquante) ; `agy models` aboutit.

Un runner indisponible est traité comme un runner évité : ses tâches partent sur leur runner de repli (explicite ou automatique, cf. §4.4) s'il est disponible, sinon le `run` échoue avant toute exécution en indiquant le motif. Le skill `plan-tasks` lit la ligne `runners disponibles` pour ne générer que des fiches exécutables.

---

## 5. Machine à états et cycle d'exécution (`agent-runner`)

### 5.1. Séquence d'exécution avec boucle d'escalade

Pour chaque fichier `todo/*.md` trié par ordre lexicographique (ignorant `.disabled`, filtré optionnellement par `--batch`) :

1. **Pré-conditions (Fail-Fast) :**
   * Vérifier que `.agent-tasks` est ignoré par Git (`git check-ignore -q .agent-tasks`).
   * Vérifier que l'arbre de travail Git est propre (`git status --porcelain --untracked-files=no` vide), sauf si `--resume` est actif.
   * Basculer sur `branch` (`git checkout <branch>`), ou la créer depuis `base_branch` (`git checkout -b <branch> <base_branch>`).
   * Enregistrer `HEAD_BEFORE = $(git rev-parse HEAD)`.

2. **Boucle de tentatives (Escalade hiérarchique) :**
   * Construire la liste des modèles candidats à partir de `model` jusqu'à `max_model` dans la chaîne du `runner` (ou uniquement `[forced_model]` si `--model` est passé en CLI et `--no-escalate` est actif).
   * Pour chaque modèle candidat `M` (tentative `k = 1, 2, ...`) :
     1. **Contexte de reprise :** Si `k > 1` (escalade après échec de la tentative `k-1`) ou si `--resume` est actif, préfixer le prompt avec :
        * Le résumé du motif d'échec précédent (code retour, sortie de `verify_cmd`, ou dépassement de `max_turns`).
        * L'état `git status --short` et `git diff --stat` afin que le modèle supérieur reparte du travail partiel existant.
     2. **Invocation CLI vierge :** Lancer le `runner` avec le modèle `M` et les drapeaux d'observabilité complète (sauvegarde du flux complet des tours dans `.agent-tasks/logs/<id>.try<k>-<M>.log` et du JSON final dans `.json` pour permettre le diagnostic post-mortem des anomalies).
     3. **Métriques FinOps :** Ajouter une ligne dans `.agent-tasks/logs/usage.tsv` :
        `timestamp | batch | id | try | runner | model | turns | tokens_in | cache_write | cache_read | tokens_out | cost_usd | duration_sec | status`
     4. **Validation des post-conditions (par le runner) :**
        * **C1 — Exécution :** Processus terminé sans timeout ni dépassement `max_turns`, code retour `0` et `is_error == false`.
        * **C2 — Propreté Git :** Aucun fichier suivi modifié non commité (`git status --porcelain --untracked-files=no` vide).
        * **C3 — Progression :** Si `require_commit: true`, vérifier que `HEAD_AFTER != HEAD_BEFORE`.
        * **C4 — Vérification métier :** Si `verify_cmd` est renseigné, exécuter la commande depuis la racine du dépôt et vérifier le code retour `0`.
     5. **Décision :**
        * Si `C1` à `C4` sont valides $\rightarrow$ sortir de la boucle en **Succès**.
        * Sinon, s'il reste un modèle supérieur dans la chaîne d'escalade $\rightarrow$ journaliser l'escalade (`haiku -> sonnet`) et passer à la tentative `k + 1` en conservant l'arbre partiel.

3. **Transition d'état finale de la tâche :**
   * **Succès :** Déplacer `.agent-tasks/todo/<fichier>.md` $\rightarrow$ `.agent-tasks/done/<fichier>.md`.
   * **Échec après épuisement de la chaîne :**
     * *Mode strict (défaut) :* Laisser le fichier dans `todo/` (avec l'état partiel intact pour inspection ou `--resume`) et interrompre immédiatement l'exécution.
     * *Mode `--continue-on-error` :* Réinitialiser l'arbre (`git reset --hard $HEAD_BEFORE` et `git clean -fd`), annexer l'historique des tentatives et motifs d'échec à la fin du fichier Markdown, déplacer vers `.agent-tasks/failed/<fichier>.md`, et passer à la tâche suivante.

### 5.2. Finalisation d'un lot (`batch`)

À la fin du traitement de la file, pour chaque `batch` ayant une `merge_strategy` différente de `none` :
1. Vérifier qu'il ne reste **aucune** tâche de ce `batch` dans `todo/` ni dans `failed/`.
2. Basculer sur `base_branch` (`git checkout <base_branch>`).
3. Exécuter le merge (`git merge --ff-only <branch>` pour `auto_ff`, ou `git merge --no-ff <branch>` pour `auto_no_ff`).
4. Si le merge réussit, supprimer la branche temporaire (`git branch -d <branch>`). En cas de conflit, avorter (`git merge --abort`), conserver `branch` intacte et signaler une erreur explicite.

---

## 6. Interface en ligne de commande (CLI)

```text
Usage:
  agent-runner init [options]           Initialise .agent-tasks/ dans le dépôt courant
    --gitignore                         Ajoute .agent-tasks/ à .gitignore au lieu de .git/info/exclude
    --with-agent-rules                  Ajoute les consignes dans CLAUDE.md / règles agy
  agent-runner run [options]            Exécute les tâches présentes dans .agent-tasks/todo/
    --batch <nom>                       Ne traite que les tâches appartenant au lot <nom>
    --dry-run                           Affiche le plan d'exécution et les chaînes d'escalade sans exécuter
    --resume                            Reprend une tâche interrompue en conservant les modifications partielles
    --continue-on-error                 En cas d'échec final, rollback la tâche vers failed/ et continue
    --model <modèle>                    Force le modèle de départ (escalade toujours active sauf --no-escalate)
    --no-escalate                       Désactive l'escalade automatique de modèle en cas d'échec
    --avoid-runner <runner>             Bascule d'emblée sur leur runner de repli les tâches de ce runner (répétable)
  agent-runner models [runner]          Affiche les runners disponibles, les chaînes, exclusions et le catalogue (agy models)
  agent-runner status                   Affiche l'avancement, le taux d'escalade et les coûts cumulés
  agent-runner retry <id>               Déplace une tâche de failed/ vers todo/ (en retirant le rapport d'erreur)
  agent-runner retry --all             Idem pour toutes les tâches de failed/
  agent-runner clean [--all]            Supprime logs/ (et done/ si --all est précisé)
```

---

## 7. Spécification du Skill de génération (`plan-tasks`)

Le skill `plan-tasks` intègre directement les bonnes pratiques de réduction de tokens et d'escalade :

1. **Séparation des rôles :** Ne modifie aucun fichier de code source lors de la planification ; initialise `.agent-tasks/` si absent et génère uniquement les fiches `.agent-tasks/todo/NN-slug.md`.
2. **Tâches autonomes (élimination des lectures redondantes) :** Chaque fiche doit contenir dans son corps **l'extrait exact du plan et les numéros de lignes / règles applicables** à cette seule étape, afin d'éviter que chaque agent ne relise l'intégralité d'un document de spécifications à chaque exécution.
3. **Optimisation des déplacements de code :** Lorsque le projet dispose de `move-lines` (ou équivalent), l'inclure dans `allowed_tools` et instruire l'agent de déplacer les plages de lignes via cet outil plutôt que de réécrire le code deux fois.
4. **Routage et niveau de départ par tâche :** chaque fiche reçoit un niveau (N1 à N3) déduit d'une grille (périmètre, nature du changement, force de `verify_cmd`, risque, ambiguïté) qui fixe `model`, `effort`, `max_model` et `max_turns` ; `runner`/`fallback_runner` sont choisis selon le profil de la tâche et le quota déclaré. Le détail (grille, table des niveaux, cas où `opus` doit être privilégié) est dans `skills/plan-tasks/SKILL.md`.
5. **Garde-fou `max_turns` et `verify_cmd` ciblé :** Fixer un `max_turns` serré (ex. `25` à `30` tours) couplé à un `verify_cmd` rapide sur le seul module modifié, afin qu'un agent qui tourne en rond sur une erreur de compilation soit interrompu tôt et relayé au modèle supérieur plutôt que de consommer 40+ tours inutiles.

## 8. Pistes d'évolution non retenues

### 8.1. Exécution parallèle des tâches (worktrees, exécution distante)

**Idée :** exécuter en parallèle des tâches indépendantes, chacune dans son propre `git worktree` ou sur un runner distant (ex. Codex cloud), en marquant dans les fiches générées par `plan-tasks` lesquelles sont parallélisables.

**Statut :** non retenue pour l'instant (gain jugé inférieur au coût).

**Motifs :**
- **Goulot = quota, pas latence :** l'outil tourne sans surveillance ; paralléliser consomme le quota plus vite sans augmenter le débit total.
- **Pas de parallélisme intra-lot :** les micro-tâches d'un `batch` s'enchaînent sur la même branche, chacune partant du commit de la précédente.
- **Coût de build multiplié :** chaque worktree a son propre répertoire de build (`target/`…), donc chaque `verify_cmd` recompile depuis zéro ; les états locaux partagés entre fiches (références générées, caches) ne suivent pas.
- **Complexité du modèle d'intégrité :** conflits de merge entre lots, accès concurrent à `todo/`→`done/`, et `finalize_batches` fait `git checkout <base_branch>`, refusé par Git si cette branche est déjà extraite dans un autre worktree.
- **Exécution distante :** environnement différent, synchronisation par push/PR, et l'orchestrateur ne peut plus valider localement les post-conditions C2–C4 (contraire au principe de contrôle d'intégrité externe).

**Alternative actuelle :** le `batch` est l'unité naturelle de parallélisme. Un parallélisme manuel reste possible sans modification de code : un worktree par lot, copie de `.agent-tasks/`, `agent-runner --batch X` avec `merge_strategy: none`, puis merge manuel.

**À reconsidérer si :** plusieurs lots longs et réellement indépendants (crates/modules disjoints) deviennent fréquents, avec de la marge de quota et un besoin réel de réduire le temps d'attente. Prérequis alors : règle d'indépendance inter-lots explicite dans `plan-tasks` (lots disjoints en fichiers et sans dépendance), plutôt qu'un champ `parallelizable` par tâche.

### Validations avant exécution et diagnostics (ajouts)

* **Cohérence de la file** (`run`, y compris `--dry-run`) : `base_branch` doit exister (ou être créée par une tâche précédente) ; `branch != base_branch` si `merge_strategy != none` ; dans un lot comportant une fusion automatique, toutes les tâches partagent `branch`, `base_branch` et `merge_strategy`. Les cibles de `[fallback_map]` absentes de la chaîne ou du catalogue du runner de destination donnent un avertissement.
* **Escalade :** le prompt de la tentative k+1 inclut aussi les commits déjà créés (`git log`/`git diff --stat <head_initial>..HEAD`) et la dernière sortie de l'agent (échecs C1/C2/C3).
* **Fichiers non suivis :** les fichiers créés par une tentative et laissés non suivis (oubli de `git add`) sont signalés par un avertissement.
* **Métriques en cas d'interruption :** `timeout` et `max_turns` envoient SIGTERM (3 s de grâce avant SIGKILL) ; sans événement `result`, les tokens sont cumulés depuis les événements `assistant` (coût inconnu, `0`).
* **Hook de fin (`on_finish_cmd`) :** commande shell lancée depuis la racine du dépôt à la fin de chaque `run` hors `--dry-run` (succès, échec, erreur fatale ou Ctrl-C), avec `AGENT_RUNNER_STATUS` (`success` | `failure` | `error`), `AGENT_RUNNER_EXIT_CODE`, `AGENT_RUNNER_TOTAL`, `AGENT_RUNNER_DONE`, `AGENT_RUNNER_FAILED`, `AGENT_RUNNER_DONE_IDS`, `AGENT_RUNNER_FAILED_IDS`, `AGENT_RUNNER_REPO`. Délai max 60 s ; son échec n'est qu'un avertissement.
