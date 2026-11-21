# Intégration avec un outil de ticketing (Jira, Linear, GitLab…)

`agent-task-runner` reste volontairement agnostique : les agents IA (`claude`, `agy`, `codex`) n'ont pas besoin d'accéder à votre outil de ticketing. La synchronisation s'effectue côté orchestrateur via le hook `on_finish_cmd` et la sortie structurée `agent-runner report --json`.

## 1. Données exposées au hook `on_finish_cmd`

Lorsqu'un `agent-runner run` se termine (succès, échec ou interruption), la commande configurée dans `.agent-tasks/config.toml` (`on_finish_cmd`) est exécutée à la racine du dépôt avec les variables d'environnement suivantes :

| Variable | Contenu |
| :--- | :--- |
| `AGENT_RUNNER_STATUS` | `success` \| `failure` \| `error` |
| `AGENT_RUNNER_EXIT_CODE` | Code retour du runner (`0`, `1`, ou vide si interrompu) |
| `AGENT_RUNNER_BATCH` | Nom(s) de lot(s) exécuté(s), séparés par des espaces |
| `AGENT_RUNNER_BRANCH` | Branche(s) cible(s), séparées par des espaces |
| `AGENT_RUNNER_TOTAL` | Nombre total de tâches du run |
| `AGENT_RUNNER_DONE` / `AGENT_RUNNER_FAILED` | Compteurs de tâches réussies / en échec |
| `AGENT_RUNNER_DONE_IDS` / `AGENT_RUNNER_FAILED_IDS` | Identifiants des tâches, séparés par des espaces |
| `AGENT_RUNNER_REPO` | Chemin absolu de la racine du dépôt |

Un code retour non nul de `on_finish_cmd` produit uniquement un avertissement sur `stderr` : il ne casse jamais le pipeline Git.

## 2. Consommer les métriques via `agent-runner report --json`

```bash
agent-runner report --batch "$AGENT_RUNNER_BATCH" --json
```

Format retourné :

```json
{
  "attempts": [
    {
      "timestamp": "2026-11-21T01:30:00+00:00",
      "batch": "PROJ-123",
      "task_id": "01-extract-service",
      "attempt": 1,
      "runner": "claude",
      "model": "haiku",
      "turns": 6,
      "tokens_in": 12400,
      "cache_write": 0,
      "cache_read": 8200,
      "tokens_out": 950,
      "cost_usd": 0.0215,
      "duration_sec": 18,
      "status": "success"
    }
  ],
  "total": {
    "count": 1,
    "turns": 6,
    "tokens_in": 12400,
    "cache_write": 0,
    "cache_read": 8200,
    "tokens_out": 950,
    "cost_usd": 0.0215,
    "duration_sec": 18
  }
}
```

## 3. Exemple projet : synchronisation Jira (stdlib Python uniquement)

1. Dans `.agent-tasks/config.toml` du projet :

```toml
on_finish_cmd = "python3 scripts/sync-jira.py"
```

2. Dans `scripts/sync-jira.py` (adapter le regex au format de clé de votre projet) :

```python
#!/usr/bin/env python3
"""Poste un commentaire récapitulatif sur Jira à la fin d'un run agent-runner."""
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request

JIRA_URL = os.environ.get("JIRA_URL", "").rstrip("/")  # ex. https://org.atlassian.net
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
if not (JIRA_URL and JIRA_TOKEN):
    sys.exit(0)  # pas configuré sur ce poste : sortie silencieuse

source = f"{os.environ.get('AGENT_RUNNER_BRANCH', '')} {os.environ.get('AGENT_RUNNER_BATCH', '')}"
match = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", source)
if not match:
    sys.exit(0)
issue_key = match.group(1)

batch = os.environ.get("AGENT_RUNNER_BATCH", "").split()
cmd = ["agent-runner", "report", "--json"] + (["--batch", batch[0]] if len(batch) == 1 else [])
report = json.loads(subprocess.check_output(cmd, text=True))
tot = report["total"]

body = (
    f"[agent-runner] {os.environ.get('AGENT_RUNNER_STATUS')} "
    f"({os.environ.get('AGENT_RUNNER_DONE')}/{os.environ.get('AGENT_RUNNER_TOTAL')} tâches)\n"
    f"Branche : {os.environ.get('AGENT_RUNNER_BRANCH')}\n"
    f"Tentatives : {tot['count']}, coût : ${tot['cost_usd']:.2f}, durée : {tot['duration_sec']}s"
)

# Jira REST API v2 (texte brut ; pour Cloud v3, utiliser un objet ADF {"type": "doc", ...})
auth = (
    "Basic " + base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    if JIRA_EMAIL
    else f"Bearer {JIRA_TOKEN}"
)
req = urllib.request.Request(
    f"{JIRA_URL}/rest/api/2/issue/{issue_key}/comment",
    data=json.dumps({"body": body}).encode(),
    headers={"Authorization": auth, "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=10):
    pass
```

3. Ne stockez jamais les jetons d'API dans `.agent-tasks/config.toml` : passez `JIRA_URL`, `JIRA_EMAIL` et `JIRA_API_TOKEN` via l'environnement du poste ou de la CI.
