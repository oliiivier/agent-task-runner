---
# Identifiant unique de la tâche (obligatoire)
id: 00-example
# Lot logique : le merge (merge_strategy) n'a lieu que lorsque toutes les tâches du lot sont réussies
batch: example
# Branche cible (obligatoire) ; créée depuis base_branch si absente
branch: refactor/example
base_branch: main
# none | auto_ff | auto_no_ff
merge_strategy: none
# Niveau intrinsèque (N1 | N2 | N3) : résolu dynamiquement au run selon [routing] et les quotas
level: N1
# Décommenter pour épingler un runner/modèle précis (sinon résolu automatiquement par `level`) :
# runner: claude
# fallback_runner: agy
# model: haiku
# max_model: sonnet
# Effort de raisonnement (low | medium | high | xhigh | max) ; omis = défaut du niveau/CLI
effort: low
escalate: true
# Garde-fous
max_turns: 30
timeout_sec: 900
require_commit: true
# Commande exécutée par le runner après l'agent (code retour 0 exigé)
verify_cmd: "git diff --quiet HEAD"
allowed_tools:
  - "Bash(git:*)"
  - "Bash(move-lines:*)"
---

## Objectif
Décrire ici, en une phrase, le changement à réaliser.

## Contexte extrait du plan (autonome — inutile de relire tout le plan)
- Extrait exact du plan et numéros de lignes concernés par CETTE tâche uniquement.
- Utiliser `move-lines` pour déplacer des blocs de lignes volumineux avant d'ajuster les imports.
- Produire un commit unique en anglais (`refactor(scope): message`).
