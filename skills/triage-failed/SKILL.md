---
name: triage-failed
description: Analyse post-mortem et réparation des fiches en échec dans `.agent-tasks/failed/`. Inspecte le rapport d'échec, les logs `.verify.log`, diagnostique la cause (spécification incomplète, quota/timeout, ou tâche trop grosse), réécrit ou scinde les fiches vers `.agent-tasks/todo/` et propose `agent-runner run`.
---

# triage-failed : diagnostic et réparation de `.agent-tasks/failed/`

Tu diagnostiques et répares les fiches échouées avant de relancer ; tu ne modifies aucun fichier de code source (sauf demande explicite de l'utilisateur).

## Étape 1 — Inventaire des échecs

1. Liste `.agent-tasks/failed/*.md`. Si le dossier est vide, indique-le et arrête.
2. Pour chaque fiche échouée, lis :
   - Le frontmatter et le corps de `.agent-tasks/failed/<id>.md` (y compris la section `<!-- agent-runner:failure-report -->`).
   - Les journaux associés dans `.agent-tasks/logs/<id>.try*.verify.log` (prioritaire si `verify_failed`) et les dernières lignes de `<id>.try*.log` si le statut est `error`, `timeout`, `max_turns`, `dirty` ou `no_commit`.
   - Si utile pour vérifier l'état réel des fichiers ciblés, utilise `file-outline <fichier>` plutôt que de lire des fichiers entiers.

## Étape 2 — Diagnostic de la cause racine

Classe chaque fiche dans l'une des catégories suivantes :

| Catégorie | Indices typiques | Action corrective |
| :--- | :--- | :--- |
| **Spécification / périmètre** | Symbole ou import introuvable, dépendance non mentionnée dans le contexte, numéros de lignes périmés (`file-outline` montre un décalage). | Complète `## Périmètre exact` (avec `file-outline`) et `## Contexte extrait du plan`, puis remets la fiche dans `todo/`. |
| **`verify_cmd` mal ciblée** | Le code produit est valide mais `verify_cmd` échoue sur un module non touché, un linter hors périmètre ou un timeout trop court. | Corrige `verify_cmd` (ou ajuste `timeout_sec`) dans le frontmatter. |
| **Tâche trop vaste / bug complexe** | `max_turns` ou `timeout` atteint après plusieurs essais, ou erreur persistante malgré `N2`/`N3`. | **Scinde** la fiche en 2 micro-tâches atomiques dans `.agent-tasks/todo/` (ex. `NN-slug-a.md` et `NN-slug-b.md`) et supprime l'ancienne fiche de `failed/`. |
| **Outillage / édition fragile** | L'agent réécrit un fichier entier ou casse l'indentation. | Ajoute `"Bash(replace-block:*)"` / `"Bash(move-lines:*)"` à `allowed_tools` et précise les plages exactes. |
| **Quota / environnement** | Statut `quota` ou erreur CLI sur un runner. | Ne modifie pas la fiche sauf si `fallback_runner: none` bloque ; propose `agent-runner run --avoid-runner <runner>`. |

## Étape 3 — Réparation vers `.agent-tasks/todo/`

- **Ne laisse jamais** le bloc `<!-- agent-runner:failure-report -->` (ni tout ce qui le suit) dans une fiche replacée dans `todo/`.
- Si tu réécris une fiche existante : écris la version corrigée dans `.agent-tasks/todo/<fichier>.md` et supprime `.agent-tasks/failed/<fichier>.md` (équivalent propre de `agent-runner retry <id>` suivi d'une édition).
- Si tu scindes une fiche en 2 micro-tâches : crée les deux fiches dans `.agent-tasks/todo/` avec le même `batch`, `branch`, `base_branch` et `merge_strategy`, puis supprime l'originale dans `.agent-tasks/failed/` (sinon `agent-runner merge` bloquera sur une tâche restante dans `failed/`).
- Avant de monter `level` vers `N3`, vérifie qu'une meilleure précision de contexte ou un découpage en 2 fiches `N1`/`N2` ne suffit pas.

## Étape 4 — Restitution

Présente un tableau récapitulatif à l'utilisateur :

| Fiche | Statut / cause identifiée | Action effectuée | Destination |
| :--- | :--- | :--- | :--- |

Termine en proposant la commande adaptée :
- `agent-runner run --dry-run` pour vérifier le routage des fiches réparées,
- puis `agent-runner run` (ou `agent-runner run --batch <lot>` / `--avoid-runner <runner>` si quota).
