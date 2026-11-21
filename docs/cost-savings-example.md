# Mesurer le gain avec `agent-runner report`

`agent-runner report` (et `report --json`) compare le coût réel d'un lot exécuté par `agent-runner` à deux scénarios de référence sur le modèle N3 de chaque runner (`opus` pour `claude`, `gemini-4-neon` pour `agy`, `gpt-5.5` pour `codex`).

## Exemple réel sur un lot mixte de 46 tâches (`claude` + `agy`)

```text
Tâche                              #  Modèle                        Tours     Entrée    Cache W    Cache R     Sortie     Coût   Durée  Statut
----------------------------------------------------------------------------------------------------------------------------------------------
001-ffi-spec                       1  agy gemini-4-hydrogen-high        1     67 238          0          0     25 595  $  0.00   2m29s  success
002-ffi-impl                       1  claude opus                      19         32     91 819  1 076 004     22 436  $  1.40   6m18s  success
003-core-model-domain-spec         1  agy gemini-3.8-flash-medium       1     70 143          0          0     27 459  $  0.00   3m54s  success
...
014-core-epub-segments-impl-p1     1  claude opus                      38         74    129 685  3 614 669     40 564  $  2.57   8m33s  success
015-core-epub-segments-impl-p2     1  claude opus                      44         78    143 970  4 144 596     44 489  $  2.87  11m23s  success
...
046-catalog-calibreweb-kobo-impl-p2 1 agy gemini-4-hydrogen-high        1     83 658          0          0     12 410  $  0.00   5m03s  success
----------------------------------------------------------------------------------------------------------------------------------------------
Total (46 tentative(s))                                               227  5 196 963    794 815  15 419 533  1 456 204  $ 15.08  289m38s
Paliers : 8 N1, 29 N2, 9 N3
Économie estimée : $21.01 réel estimé (dont $15.08 facturé) vs ~$89.79 en tout-N3 (-77 %, borne basse à tokens identiques ; le gain réel est encore supérieur grâce à l'absence de cumul de contexte)
Session unique N3 (modélisée, recouvrement fichiers 78 %, plafond compaction 150k) : ~$114.04
```

## Comment lire chaque ligne

1. **`Paliers : 8 N1, 29 N2, 9 N3`**
   Répartition des tentatives selon le niveau intrinsèque du modèle utilisé. Sur cet exemple, **80 % des tâches (37/46)** ont réussi en `N1`/`N2` (`gemini-3.8-flash` / `gemini-4-hydrogen`), préservant les quotas Claude pour les 9 tâches complexes `N3`.

2. **`$21.01 réel estimé (dont $15.08 facturé)`**
   - **`$15.08 facturé`** : somme exacte des `cost_usd` remontés par les CLI (`claude` remonte son coût réel ; `agy` remonte `$0.00`).
   - **`$21.01 réel estimé`** : ajoute l'équivalent API estimé (tarifs de référence `$/MTok` dans `adapters/`) pour les runners qui remontent `$0.00`, afin de comparer à périmètre égal avec le scénario tout-N3.

3. **`~$89.79 en tout-N3 (-77 %, borne basse à tokens identiques)`**
   Coût contrefactuel si exactement les mêmes tokens avaient été consommés sur le modèle N3 de chaque runner. C'est une **borne basse** car elle ne compte pas l'explosion de contexte d'une session unique.

4. **`Session unique N3 (modélisée, recouvrement fichiers 78 %, plafond compaction 150k)` -> `~$114.04`**
   Modélise l'exécution de tout le lot dans une **seule session interactive** :
   - **Effet favorable du cache (recouvrement)** : en lisant les fiches dans `.agent-tasks/{done,failed,todo}`, `agent-runner` mesure la part de fichiers déjà lus par une tâche précédente. Cette fraction est basculée en `cache_read` (tarif réduit : `-90 %` sur Claude, `-75 %` sur Gemini) au lieu d'être rechargée plein tarif.
   - **Effet défavorable du cumul** : à chaque tour d'une tâche, l'historique accumulé des tâches précédentes est ré-envoyé en `cache_read`, avec un **plafond de compaction réaliste à 150 000 tokens** (une session réelle compacte au lieu de croître à l'infini).

## Limites connues
- Les tarifs `$/MTok` sont des tarifs API publics de référence définis dans `adapters/` (`claude.py`, `agy.py`, `codex.py`) ; les CLI ne les exposent pas dynamiquement.
- Sur `agy`, le JSON de sortie ne détaille pas toujours le nombre de tours internes (`Tours = 1`) ni le cache ; la pénalité de cumul sur les tâches `agy` est donc sous-estimée.
- Si `agent-runner clean --all` a supprimé `done/`, le recouvrement de fichiers vaut `0 %`.
