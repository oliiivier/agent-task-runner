---
name: review-batch
description: "Audit de cohérence inter-tâches d'un lot terminé avant `agent-runner merge`. Inspecte `git diff <base_branch>...<branch>` (ou génère une fiche d'audit `99-audit-*.md` avec `prefer_runner: agy` et `require_commit: false`) pour détecter code mort, doublons ou incohérences de nommage."
---

# review-batch : audit de cohérence inter-tâches avant merge

Même lorsque chaque micro-tâche passe son `verify_cmd` unitaire, une série de commits sur une branche de lot peut introduire du code mort, des doublons entre deux fiches ou des incohérences de nommage. Ce skill audite le diff global du lot avant `agent-runner merge`.

## Étape 1 — Identifier le lot et ses branches

1. Lance `agent-runner status` (ou inspecte `.agent-tasks/done/`, `todo/`, `failed/`).
2. Vérifie s'il reste des fiches dans `todo/` ou `failed/` pour ce lot :
   - S'il en reste, signale-le à l'utilisateur (propose `triage-failed` si `failed/` n'est pas vide).
3. Récupère `batch`, `branch` et `base_branch` depuis les fiches de `.agent-tasks/done/` du lot ciblé (ou demande à l'utilisateur quel `batch` auditer s'il y en a plusieurs).

## Étape 2 — Analyser le diff complet du lot

Exécute :
- `git log --oneline <base_branch>..<branch>` pour voir l'enchaînement des commits du lot,
- `git diff --stat <base_branch>...<branch>` pour mesurer l'ampleur du diff,
- `git diff <base_branch>...<branch>` (ou fichier par fichier si le diff est très volumineux, en t'appuyant sur `file-outline` pour situer les symboles).

### Grille d'audit inter-tâches

Vérifie systématiquement :
1. **Code mort et reliquats** : fonctions, classes, imports, variables ou fichiers devenus inutilisés après un déplacement (`move-lines`) ou une refonte sur plusieurs fiches.
2. **Doublons inter-fiches** : helpers ou logique quasi identiques implémentés séparément dans deux tâches du même lot.
3. **Cohérence de nommage et d'API** : conventions hétérogènes entre tâches (noms de symboles, signatures, messages de log/erreur, commentaires/docstrings).
4. **Invariants globaux et tests** : cas où les `verify_cmd` unitaires étaient trop étroits et nécessitent une passe globale (tests d'intégration, lint, build complet).

## Étape 3 — Décider : audit direct ou fiche d'audit autonome

- **Cas A — Diff lisible directement** : réalise l'audit immédiatement et classe les constats en :
  - **Bloquants** (à corriger avant `merge`),
  - **Mineurs / suggestions** (optionnels).
  Si des corrections sont nécessaires, propose de créer une ou plusieurs micro-tâches de nettoyage dans `.agent-tasks/todo/` (même `batch`, `branch`, `base_branch`) plutôt que de modifier le code à la main sans traçabilité.
- **Cas B — Diff très large (> 1 500 lignes) ou demande de passe autonome** : génère une fiche d'audit finale `.agent-tasks/todo/99-audit-<batch>.md` configurée pour profiter du contexte 1M+ d'`agy` sans exiger de commit si rien n'est cassé :

```markdown
---
id: 99-audit-<batch>
batch: <batch>
branch: <branch>
base_branch: <base_branch>
merge_strategy: none
# logic:local | read:large | write:1f | verify:strong | amb:bounded | tools:standard
level: N2
effort: medium
prefer_runner: agy
fallback_runner: claude
escalate: false
require_commit: false
max_turns: 20
timeout_sec: 900
verify_cmd: "<commande globale de test/build du projet>"
---

## Objectif
Auditer la cohérence globale de `git diff <base_branch>...<branch>` avant fusion et exécuter la vérification globale.

## Périmètre exact
- `git diff <base_branch>...<branch>`
- `git log --oneline <base_branch>..<branch>`

## Contexte extrait du plan
- Vérifier : code mort, imports inutilisés, doublons entre tâches, cohérence de nommage.
- Ne rien modifier si tout est cohérent ; si un défaut mineur est corrigé, produire un commit en anglais (`chore(audit): ...`).
```

## Étape 4 — Passage au merge

- Si aucun problème bloquant n'est détecté (et aucune fiche restante dans `todo/` ou `failed/`), propose :
  - `agent-runner merge --batch <batch> --dry-run`
  - puis `agent-runner merge --batch <batch>`
- Sinon, propose d'exécuter d'abord les fiches correctives avec `agent-runner run --batch <batch>` avant de fusionner.
