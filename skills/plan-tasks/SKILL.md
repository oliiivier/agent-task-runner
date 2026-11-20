---
name: plan-tasks
description: Découpe un chantier (plan, refactor, spécification) en micro-tâches atomiques `.agent-tasks/todo/NN-slug.md` exécutables par `agent-runner`, avec choix du runner (claude/agy), du modèle et de l'effort adaptés à chaque tâche. À utiliser quand l'utilisateur veut planifier/découper un travail pour exécution autonome par agent-runner.
---

# plan-tasks : génération de la file de tâches pour `agent-runner`

Tu planifies ; tu n'exécutes pas. Chaque fiche sera exécutée plus tard par un agent non interactif, en contexte vierge, qui ne verra que la fiche.

## Règles

1. **Séparation des rôles.** Ne modifie AUCUN fichier de code source. Si `.agent-tasks/` est absent, lance `agent-runner init`. Écris uniquement des fiches `.agent-tasks/todo/NN-slug.md` (numérotation à 2 chiffres, ordre = ordre d'exécution).
2. **Tâches autonomes.** Le corps de chaque fiche contient l'extrait EXACT du plan, les numéros de lignes et les règles applicables à cette seule étape (`## Objectif`, `## Contexte extrait du plan (autonome — inutile de relire tout le plan)`). Une fiche précise permet à un modèle léger de réussir du premier coup.
3. **Déplacements de code.** Si `move-lines` est disponible, ajoute `"Bash(move-lines:*)"` à `allowed_tools` et demande explicitement d'utiliser `move-lines <src> <début> <fin> <dst>` plutôt que de réécrire le code.
4. **Runners disponibles et validation des modèles.** Lance `agent-runner models` avant de générer les fiches. Sa première ligne (`runners disponibles : …`) fait foi : n'écris dans `runner` et `fallback_runner` **que des runners disponibles** (installés, connectés et autorisés par `runners` dans `config.toml`). Si un seul est disponible, utilise-le partout, n'écris pas de `fallback_runner` vers le runner absent (`none` reste valable pour interdire le repli) et ignore les consignes propres à l'autre runner ; signale à l'utilisateur le runner indisponible et son motif. `model` et `max_model` doivent appartenir à la chaîne `[escalation]` du runner (sinon la fiche est rejetée). Pour `agy`, indique uniquement la **famille** (`gemini-4-argon`), `effort` compose la variante (`<famille>-<effort>`, ou l'effort disponible le plus proche — ex. `xhigh` devient `high` sur `argon`) ; n'écris jamais d'identifiant `claude-*` pour `agy` (quota très réduit).
5. **Vérification rapide et ciblée (`verify_cmd`).** Fixe un `verify_cmd` rapide et limité au module modifié (sauf passe finale globale ou audit), pour qu'un échec soit détecté vite et relayé au modèle supérieur sans ralentir chaque tentative. Un agent qui tourne en rond est coupé par `max_turns` (claude) ou `timeout_sec` (agy). La fiabilité de `verify_cmd` conditionne le niveau.
6. **Un commit par tâche** (`require_commit: true`, message en anglais, format conventionnel), sauf baseline/audit (`require_commit: false`).
7. **Cohérence de lot.** Regroupe les tâches dépendantes dans un même `batch` avec la même `branch` et fixe `merge_strategy` (`none`, `auto_ff`, `auto_no_ff`).

## Choisir le niveau

Évalue chaque tâche sur cinq critères ; **le critère le plus élevé fixe le niveau** (pas la moyenne). Une tâche N3 trop grosse se découpe d'abord plutôt que d'être compensée par un modèle plus lourd.

| Critère | N1 | N2 | N3 |
| :--- | :--- | :--- | :--- |
| **Nature** | déplacement, renommage, format, doc, test calqué sur existant | logique locale, nouvelle fonction, adaptation signatures/visibilité | architecture, invariants inter-modules, comportement subtil |
| **Périmètre** | 1 fichier (ou 2 avec `move-lines`) | 1 à 3 fichiers d'un même module | plusieurs modules ou API publique |
| **Vérification** | compile + tests ciblés couvrant le changement | compile + quelques tests | faible/absente, ou échec à l'exécution uniquement |
| **Risque** | nul | local | données, sécurité, concurrence, migration |
| **Ambiguïté** | aucune : extrait et lignes exacts | quelques choix d'implémentation | décisions de conception laissées à l'agent |

| Niveau | `claude` | `agy` (chaîne : `gemini-3.8-flash < gemini-4-hydrogen < gemini-4-argon < gemini-4-neon`) | `max_turns` | `timeout_sec` |
| :--- | :--- | :--- | :---: | :---: |
| **N1 — Mécanique** | `model: haiku`, `effort: low`, `max_model: sonnet` | `model: gemini-3.8-flash`, `effort: low`, `max_model: gemini-4-hydrogen` | 20 | 600 |
| **N2 — Logique locale** | `model: sonnet`, `effort: medium`, `max_model: opus` | `model: gemini-4-argon`, `effort: medium`, `max_model: gemini-4-neon` | 30 | 900 |
| **N3 — Transverse / risqué** | `model: opus`, `effort: high`, `max_model: fable` | `model: gemini-4-neon`, `effort: high` (`xhigh` si très difficile) | 40 | 1500 |
| **Audit / baseline** | `model: sonnet`, `effort: low`, `escalate: false`, `require_commit: false` | `model: gemini-4-argon`, `effort: low`, `escalate: false`, `require_commit: false` | 20 | 600 |

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

- **`runner: claude`** (défaut, `fallback_runner: agy`) : édition chirurgicale, `move-lines`, boucles courtes compilation/test. Seul runner à appliquer `allowed_tools`, `max_turns` et `max_budget_usd`.
- **`runner: agy`** (`fallback_runner: claude`) : lecture transverse multi-fichiers, documentation/audits, très large contexte. Comme `agy` ignore `allowed_tools` et `max_turns`, fixe toujours un `timeout_sec` serré dès qu'une tâche peut tourner sur `agy` (nominal ou repli). Un N3 `agy` démarre déjà au sommet de sa chaîne (`gemini-4-neon`) : sans escalade interne possible, `fallback_runner: claude` est indispensable.
- **Repli systématique :** écris un `fallback_runner` explicite sur **toutes** les tâches qui le permettent (lisible dans `--dry-run`). Un champ absent vaut repli automatique sur l'autre runner (`auto_fallback = true`, défaut) : il ne protège donc rien. Pour les tâches exigeant une liste blanche d'outils stricte ou touchant à du code de sécurité critique, écris **`fallback_runner: none`**.
- **Bascule dynamique et quotas :** en cas de saturation fournisseur à l'exécution, `agent-runner` marque le runner indisponible et bascule sur `fallback_runner` via `[fallback_map]` en conservant le travail partiel. Si l'utilisateur signale un quota bas avant lancement, garde le routage nominal dans les fiches et recommande `agent-runner run --avoid-runner <runner>` (n'inverse `runner`/`fallback_runner` dans les fiches que pour un choix permanent).

## Format d'une fiche

Champs autorisés uniquement (voir `templates/example-task.md` et `schemas/task.schema.json` du dépôt `agent-task-runner`) : `id`, `branch` (obligatoires) ; `batch`, `base_branch`, `merge_strategy`, `runner`, `fallback_runner`, `model`, `effort`, `escalate`, `max_model`, `verify_cmd`, `require_commit`, `allowed_tools`, `max_turns`, `timeout_sec`, `max_budget_usd` (optionnels).

```markdown
---
id: 10-extract-remotesync
batch: split-reader
branch: refactor/split-reader-screen
base_branch: main
merge_strategy: auto_no_ff
# N1 : déplacement pur, vérifié par compilation
runner: claude
fallback_runner: agy
model: haiku
effort: low
escalate: true
max_model: sonnet
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

## Contexte extrait du plan (autonome — inutile de relire tout le plan)
...
```

## Fin

Termine par un tableau des fiches créées : id, niveau (avec le critère qui l'a fixé), `runner → fallback_runner`, `model`/`effort → max_model`. Propose ensuite `agent-runner run --dry-run` (qui affiche le runner effectif et la chaîne de chaque tâche), puis `agent-runner run` (avec `--avoid-runner <runner>` si l'utilisateur a signalé un quota bas).
