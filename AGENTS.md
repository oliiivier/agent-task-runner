# Carte du projet agent-runner

## Carte « besoin → fichier »
- Escalade de modèle / routage / quotas : `agent_runner/routing.py`
- Parsing des fiches : `agent_runner/task.py` + `agent_runner/frontmatter.py`
- Configuration globale / locale : `agent_runner/config.py`
- Opérations Git : `agent_runner/gitutil.py`
- File de tâches / validation : `agent_runner/queue.py`
- Construction des prompts : `agent_runner/prompt.py`
- Exécution d'une tentative : `agent_runner/execute.py`
- Merge de lot : `agent_runner/merge.py`
- Détection runners / catalogues de modèles : `agent_runner/probe.py`
- Formatage et affichage console : `agent_runner/ui.py`
- Rendu et consultation des logs : `agent_runner/logs.py` (formatage Claude/Codex et `Style` : `agent_runner/log_format.py`)
- Commandes CLI `agent-runner <cmd>` : `agent_runner/commands/<cmd>.py` (`status`, `retry`, `clean` dans `misc.py`)
- Nouveau runner : `agent_runner/adapters/` (hériter de `base.py`) + déclarer dans `ADAPTERS`
- Point d'entrée CLI et parser d'arguments : `agent_runner/cli.py`

## Règles d'architecture et dépendances
- Ordre des couches (sans import circulaire) :
  `cli > commands/* > execute, routing, merge, queue, prompt, probe > task, config, gitutil, frontmatter, ui`
- Un module n'importe jamais une couche supérieure.
- `adapters/*` ne dépend que de `adapters/base.py` et de la bibliothèque standard.
- Imports paresseux des commandes dans `agent_runner/cli.py`.
- Constantes partagées centralisées dans `agent_runner/task.py` (pas de `constants.py`).

## Environnement et tests
- Bibliothèque standard Python 3.11+ uniquement (aucune dépendance tierce / pas de pip).
- Exécution des tests : `python3 -m unittest discover -s tests`
- Inspection de fichier : privilégier `file-outline <fichier>` avant de lire un fichier entier.
