---
name: plan-tasks
description: Découpe un chantier (plan, refactor, spécification) en micro-tâches atomiques `.agent-tasks/todo/NN-slug.md` exécutables par `agent-runner`, avec choix du runner (claude/agy), du modèle et de l'effort adaptés à chaque tâche. À utiliser quand l'utilisateur veut planifier/découper un travail pour exécution autonome par agent-runner.
---

# plan-tasks : génération de la file de tâches pour `agent-runner`

Tu planifies ; tu n'exécutes pas. Chaque fiche sera exécutée plus tard par un agent non interactif, en contexte vierge, qui ne verra que la fiche.

## Règles

1. **Séparation des rôles.** Ne modifie AUCUN fichier de code source. Si `.agent-tasks/` est absent, lance `agent-runner init`. Écris uniquement des fiches `.agent-tasks/todo/NN-slug.md` (numérotation à 2 chiffres, ordre = ordre d'exécution).
2. **Tâches autonomes et minimisation du contexte.** Le corps de chaque fiche contient l'extrait EXACT du plan, les numéros de lignes, les règles applicables à cette seule étape (`## Objectif`, `## Périmètre exact (fichiers et lignes à lire uniquement)`, `## Contexte extrait du plan`). Une fiche précise évite les lectures exploratoires coûteuses et permet à un modèle léger de réussir du premier coup. Si une tâche exige de lire > 4 fichiers, scinde-la ou pose `prefer_runner: agy` (fenêtre 1M+).
3. **Helpers déterministes (`file-outline`, `move-lines`, `replace-block`).**
   - Utilise `file-outline <fichier>` pendant la planification pour extraire les numéros de lignes exacts (plans Markdown, code, Terraform, Ansible) dans `## Périmètre exact` ; ajoute `"Bash(file-outline:*)"` à `allowed_tools` sur les fiches exploratoires.
   - Pour les déplacements de blocs, si `move-lines` est disponible, ajoute `"Bash(move-lines:*)"` à `allowed_tools` et demande explicitement `move-lines <src> <début> <fin> <dst>`.
   - Pour les remplacements chirurgicaux de blocs sur fichiers complexes/indentés, ajoute `"Bash(replace-block:*)"` à `allowed_tools` (`replace-block --expected-lines N <fichier> <début> <fin> -`).
4. **Runners disponibles, capacités et validation des modèles.** Lance `agent-runner models` avant de générer les fiches.
   - **Disponibilité :** Sa première ligne (`runners disponibles : …`) fait foi : n'écris dans `runner` et `fallback_runner` **que des runners disponibles** (installés, connectés et autorisés par `runners` dans `config.toml`). Si un seul est disponible, utilise-le partout, n'écris pas de `fallback_runner` vers le runner absent (`none` reste valable pour interdire le repli) et ignore les consignes propres à l'autre runner ; signale à l'utilisateur le runner indisponible et son motif.
   - **Capacités, fenêtres d'usage et profils :** `agent-runner models` affiche pour chaque runner :
     1. Ses **fenêtres d'usage en direct** : `session (5h)` et `semaine (7j)` (taux d'utilisation en % et délai avant reset). **Critère hautement discriminant** pour éviter les blocages immédiats (HTTP 429).
     2. Ses **capacités fonctionnelles** : `allowed_tools`, `max_turns`, `max_budget`, `timeout`.
     3. Son **usage recommandé** et les fiches par modèle (`[Niveau | Contexte | Coût] : description`).
     Prends impérativement en compte ces données pour décider du runner et du modèle de chaque fiche (voir *Choisir le runner et le repli*).
   - **Validation de chaîne :** `model` et `max_model` doivent appartenir à la chaîne `[escalation]` du runner (sinon la fiche est rejetée). Pour `agy`, indique uniquement la **famille** (`gemini-4-argon`), `effort` compose la variante (`<famille>-<effort>`, ou l'effort disponible le plus proche — ex. `xhigh` devient `high` sur `argon`) ; n'écris jamais d'identifiant `claude-*` pour `agy` (quota très réduit).
5. **Vérification rapide et ciblée (`verify_cmd`).** Fixe un `verify_cmd` rapide et limité au module modifié (sauf passe finale globale ou audit), pour qu'un échec soit détecté vite et relayé au modèle supérieur sans ralentir chaque tentative. Un agent qui tourne en rond est coupé par `max_turns` (claude) ou `timeout_sec` (agy). La fiabilité de `verify_cmd` conditionne le niveau.
6. **Un commit par tâche** (`require_commit: true`, message en anglais, format conventionnel), sauf baseline/audit (`require_commit: false`).
7. **Cohérence de lot.** Regroupe les tâches dépendantes dans un même `batch` avec la même `branch` et fixe `merge_strategy` (`none`, `auto_ff`, `auto_no_ff`).

## Choisir le niveau (6 métriques objectives)

Évalue chaque sous-tâche sur ces **6 métriques** (sans effet cliquet vers `N3`), et consigne le résumé sur une ligne de commentaire dans le frontmatter (`# logic:... | read:... | write:... | verify:... | amb:... | tools:...`) :

1. **`logic_depth`** (`none` | `local` | `systemic`) : `none` = transformation syntaxique/`move-lines`/doc ; `local` = fonction ou cas limites d'un module ; `systemic` = invariants inter-modules, concurrence, FFI/`unsafe`, sécurité/crypto.
2. **`read_footprint`** (`compact` | `large`) : `compact` = 1–3 fichiers (< 1 500 lignes) ; `large` = > 3 fichiers transverses ou specs entières → pose `prefer_runner: agy` (1M+ tokens).
3. **`write_footprint`** (`1f` | `2-3f` | `multi`) : au-delà de 3 fichiers modifiés (`multi`), **scinde la fiche** plutôt que de monter le modèle.
4. **`verification_strength`** (`strong` | `compile_only` | `weak`) : `strong` = compile + tests unitaires ciblés couvrant le changement ; `compile_only` = typage/build seul ; `weak` = runtime non testé automatiquement.
5. **`spec_ambiguity`** (`zero` | `bounded` | `open`) : pilote `effort` (`zero` → `low`, `bounded` → `medium`, `open` → `high`). Tranche les choix d'architecture dans la fiche autant que possible avant d'accepter `open`.
6. **`tool_strictness`** (`standard` | `strict`) : si `strict` (`move-lines` imposé ou `max_turns` serré), pose `prefer_runner: claude` (ou `runner: claude` si `strict_allowed_tools = true`).

| Combinaison estimée | `level` | `effort` | `prefer_runner` | `allow_premium` |
| :--- | :---: | :---: | :--- | :---: |
| `logic:none` + `amb:zero` | `N1` | `low` | `agy` (ou `claude` si `move-lines`) | `false` |
| `logic:local` + `verify:strong` + `amb:zero` | `N1` *(escalade N2)* | `medium` | selon `read_footprint` | `false` |
| `logic:local` + `amb:bounded` | `N2` | `medium` | `agy` si `read:large`, sinon défaut | `false` |
| `logic:local` + (`verify:compile_only` ou `amb:open`) | `N2` | `high` | selon `read_footprint` | `true` *(autorise `argon`/`opus` en escalade)* |
| `logic:systemic` (motif bloquant explicite) | `N3` | `high` | `claude` (ou `agy` si `read:large`) | `true` |

- **Motif obligatoire pour `N3` et `allow_premium: true`** : cite le critère bloquant en commentaire (concurrence, FFI/mémoire, invariant multi-modules, sécurité, `verify:weak`). Sinon reste en `N1`/`N2` avec `allow_premium: false`.

| Niveau | `claude` | `agy` (chaîne : `gemini-3.8-flash < gemini-4-hydrogen < gemini-4-argon < gemini-4-neon`) | `max_turns` | `timeout_sec` |
| :--- | :--- | :--- | :---: | :---: |
| **N1 — Mécanique** | `model: haiku`, `effort: low`, `max_model: sonnet` | `model: gemini-3.8-flash`, `effort: low`, `max_model: gemini-4-hydrogen` | 20 | 600 |
| **N2 — Logique locale** | `model: sonnet`, `effort: medium`, `max_model: opus` | `model: gemini-4-hydrogen`, `effort: medium`, `max_model: gemini-4-argon` | 30 | 900 |
| **N3 — Transverse / risqué** | `model: opus`, `effort: high`, `max_model: fable` | `model: gemini-4-neon`, `effort: high` (`xhigh` si très difficile) | 60 | 1500 |
| **Audit / baseline** | `model: sonnet`, `effort: low`, `escalate: false`, `require_commit: false` | `model: gemini-4-hydrogen`, `effort: low`, `escalate: false`, `require_commit: false` | 20 | 600 |

* **`codex`** (optionnel, seulement s'il figure dans `runners disponibles`) : pas de colonne dédiée ; prends sa chaîne `[escalation].codex` affichée par `agent-runner models` — N1 = premier maillon, N2 = maillon médian, N3 = dernier (sans escalade possible : prévoir un repli) — avec les mêmes `effort`, `max_turns` et `timeout_sec` que la ligne du niveau. Comme `agy`, il ignore `allowed_tools` et le budget.
* Toujours `escalate: true` (sauf audit). L'effort reste constant lors de l'escalade.
* **Logique économique :** `haiku` coûte ~20× moins que `sonnet`, mais `opus` ne coûte que ~2× plus que `sonnet` (et `fable` ~2× plus qu'`opus`). Ne démarre sur `haiku` que sur du purement mécanique ; sur une tâche N3, partir directement d'`opus` coûte moins cher qu'un échec `sonnet → opus` laissant un état partiel.
* **Ajustements :** sur une série de tâches identiques, si la première réussit, les suivantes peuvent descendre d'un niveau. Une tâche N2 couverte par des tests unitaires ciblés dans `verify_cmd` peut démarrer en N1 (`haiku`, `effort: medium`). `fable` sert uniquement de plafond, jamais de point de départ (sauf demande explicite ou `retry` après échec `opus`).

### Quand privilégier N3 (`opus` / `gemini-4-neon`) dès le départ
- **Concurrence et asynchronisme** : verrous, coroutines/threads, ordre d'événements, conditions de course.
- **Frontières de langage et mémoire** : FFI/JNI/bindings, `unsafe`, durées de vie, sérialisation binaire.
- **Refactoring transverse** préservant des invariants implicites entre modules, ou changement irréversible d'API/schéma/données.
- **Code sensible** : authentification, permissions, cryptographie, gestion de secrets.
- **Diagnostic de cause inconnue** : bug intermittent, test *flaky*, régression de performance.
- **Vérification faible ou absente** : pas de test couvrant la zone ; l'agent doit raisonner juste du premier coup.
- **Passe de stabilisation finale** (`check-all` multi-modules) : erreurs hétérogènes à corriger sans casser le reste.
- **Fiche nécessairement ambiguë** : choix de conception non tranchables à la planification.

## Choisir le runner et le repli

Aligne le choix du runner sur les **fenêtres d'usage réelles** (session et semaine), les capacités fonctionnelles et la taille de contexte affichées par `agent-runner models` :

### 1. Analyse discriminante des fenêtres d'utilisation (Session 5h & Semaine 7j)

Examine en premier lieu la ligne `fenêtres d'usage` renvoyée par `agent-runner models` :
- **Runner saturé ou en alerte (> 70-75%)** :
  - Si la **session (5h)** ou la **semaine** d'un runner (souvent `claude`) dépasse **75%**, **ne planifie PAS les tâches sur ce runner**. Bascule le nominal vers l'autre runner (`runner: agy`, `fallback_runner: claude`) pour éviter le blocage immédiat à l'exécution (erreur HTTP 429).
  - Si la fenêtre hebdomadaire est critique (> 85%), réserve impérativement le runner saturé aux seules micro-tâches chirurgicales impossibles sur l'autre runner.
- **Quota spécifique `argon (7j)` sur `agy`** :
  - `agy` expose une 3e métrique dédiée : `argon (7j)`. Si **`argon (7j) > 60%`**, **ne jamais démarrer sur `gemini-4-argon`** (y compris en N2) : démarre sur `gemini-4-hydrogen` et réserve `argon` uniquement aux escalades N2 ou aux audits critiques.
- **Répartition intelligente si marge disponible (< 70%)** :
  - **Préservation de la session 5h de Claude** : délester systématiquement les tâches mécaniques N1 et la lecture/documentation vers `agy` (`gemini-3.8-flash`), afin de conserver le quota horaire et hebdomadaire de Claude pour les tâches complexes N2/N3.
  - **Lots volumineux** : sur un lot de plus de 5 tâches, panache les runners selon la nature des tâches au lieu de tout router sur un seul fournisseur.

### 2. Critères d'affectation technique du runner

1. **`runner: claude` REQUIS (outillage strict ou boucle fermée)** :
   - La tâche nécessite une liste blanche d'outils (`allowed_tools` non vide, ou `strict_allowed_tools = true` dans `config.toml`). Seul `claude` applique `allowed_tools`.
   - La tâche nécessite un plafond strict de tours (`max_turns`) ou de budget (`max_budget_usd`) pour couper net d'éventuelles boucles de compilation/test.
   - Édition chirurgicale fine avec `move-lines` et boucles courtes compilation/test.
   - Tâche N3 sensible (sécurité, cryptographie, modifications critiques) nécessitant un confinement maximal.

2. **`runner: agy` PRIVILÉGIÉ (large contexte ou économie de quotas)** :
   - **Large contexte de lecture** : tâche nécessitant la lecture transverse de nombreux fichiers (> 3 fichiers), de la documentation complète, ou un audit de codebase (fenêtre 1M+ tokens évitant la saturation de contexte).
   - **Économie de quotas Claude / FinOps** : délester les tâches mécaniques N1 (déplacements, renommage, documentation) vers `agy` (`gemini-3.8-flash` ou `gemini-4-hydrogen`) pour préserver les quotas de session/semaine de Claude.
   - **`default_runner: agy`** configuré dans `.agent-tasks/config.toml` (respecte ce choix sauf si `allowed_tools` strict est exigé).
   - *Rappel :* Comme `agy` ignore `allowed_tools` et `max_turns`, fixe toujours un `timeout_sec` serré dès qu'une tâche peut tourner sur `agy` (nominal ou repli). Un N3 `agy` démarre déjà au sommet de sa chaîne (`gemini-4-neon`) : sans escalade interne possible, `fallback_runner: claude` est indispensable.

3. **`runner: codex` (optionnel)** :
   - Exécution standard OpenAI si disponible, sans restriction d'outils, avec suivi approché de `max_turns`.

4. **Arbitrage et runner par défaut :**
   - Si les fenêtres d'usage le permettent et qu'aucun critère technique strict n'impose l'un plutôt que l'autre, prends en priorité le `default_runner` configuré dans `.agent-tasks/config.toml` (affiché par `agent-runner models`).

### Repli systématique (`fallback_runner`)

- Écris un `fallback_runner` explicite sur **toutes** les tâches qui le permettent (lisible dans `--dry-run`).
- Un champ absent vaut repli automatique sur l'autre runner (`auto_fallback = true`, défaut).
- Pour les tâches exigeant une liste blanche d'outils stricte ou touchant à du code de sécurité critique, écris impérativement **`fallback_runner: none`**.

### Gestion des quotas et répartition

- Si l'utilisateur exprime une contrainte de quota au moment du prompt (ex. *"préserve mon quota Claude"*, *"utilise Gemini pour les tâches simples"*), applique ce routage via `prefer_runner` (ou `runner` + `fallback_runner: none` uniquement s'il interdit tout repli).
- En cas de saturation imprévue pendant l'exécution, `agent-runner` bascule dynamiquement sur `fallback_runner` via `[fallback_map]`. L'option `agent-runner run --avoid-runner <runner>` permet également d'éviter temporairement un runner saturé sans modifier les fiches.

## Format d'une fiche

**Règle de routage dynamique (`level` + `prefer_runner` par défaut) :**
- Écris **`level: N1`**, **`level: N2`** ou **`level: N3`** dans le frontmatter et **omets `runner`, `model` et `max_model`** par défaut : `agent-runner` résoudra dynamiquement le couple runner/modèle au moment du `run` (et l'affichera dans `--dry-run`) selon `[routing.level_runner]`, les capacités requises et les fenêtres d'usage en direct.
- Utilise **`prefer_runner: agy | claude`** pour orienter le choix (ex. `prefer_runner: agy` pour > 3 fichiers à lire, `prefer_runner: claude` pour édition chirurgicale `move-lines`) **tout en conservant la bascule automatique sur quota**.
- **Ne pinne `runner: claude` (et éventuellement `model`/`max_model`) que si une contrainte dure l'impose** : outillage restreint strict (`allowed_tools` + `strict_allowed_tools`), plafond financier (`max_budget_usd`), code de sécurité critique (`fallback_runner: none`), ou demande explicite de l'utilisateur.

Champs autorisés uniquement (voir `templates/example-task.md` et `schemas/task.schema.json` du dépôt `agent-task-runner`) : `id`, `branch` (obligatoires) ; `batch`, `base_branch`, `merge_strategy`, `level`, `runner`, `prefer_runner`, `fallback_runner`, `model`, `effort`, `escalate`, `allow_premium`, `max_model`, `verify_cmd`, `require_commit`, `allowed_tools`, `max_turns`, `timeout_sec`, `max_budget_usd` (optionnels).

```markdown
---
id: 10-extract-remotesync
batch: split-reader
branch: refactor/split-reader-screen
base_branch: main
merge_strategy: auto_no_ff
# logic:none | read:compact | write:2f | verify:compile_only | amb:zero | tools:strict
level: N1
effort: low
prefer_runner: claude
allow_premium: false
max_turns: 20
timeout_sec: 600
verify_cmd: "tools/check-android.sh :feature:reader:compileDebugKotlin"
allowed_tools:
  - "Bash(git:*)"
  - "Bash(move-lines:*)"
  - "Bash(tools/check-android.sh:*)"
---

## Objectif
...

## Périmètre exact (fichiers et lignes à lire uniquement)
- `chemin/vers/Fichier.kt` (lignes 42–118) — ne lire aucun autre fichier sauf erreur de compilation.

## Contexte extrait du plan (autonome — inutile de relire tout le plan)
...
```

## Fin

Termine par un tableau des fiches créées : id, `level` (avec le critère qui l'a fixé), `runner` (`auto` si routé par `level`, ou runner épinglé) et `fallback_runner`. Propose ensuite `agent-runner run --dry-run` (qui affiche la résolution dynamique runner/modèle et sa raison pour chaque tâche), puis `agent-runner run`.
