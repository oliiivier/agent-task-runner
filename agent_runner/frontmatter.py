"""Parsing du sous-ensemble YAML pour le frontmatter des fiches de tâches."""
from __future__ import annotations

import json
import re


def _strip_comment(raw: str) -> str:
    """Retire un commentaire YAML de fin de ligne (` # ...`) hors guillemets."""
    quote = None
    for i, ch in enumerate(raw):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or raw[:i].strip() in ("", "[") or raw[i - 1] in " ,["):
            quote = ch
        elif ch == "#" and (i == 0 or raw[i - 1].isspace()):
            return raw[:i]
    return raw


def _scalar(raw: str):
    raw = _strip_comment(raw).strip()
    if raw == "" or raw in ("null", "~"):
        return None
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip('"')
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return [_scalar(p) for p in raw.strip("[]").split(",") if p.strip()]
    if re.fullmatch(r"-?(0|[1-9]\d*)", raw):  # `01` reste une chaîne (identifiant), pas l'entier 1
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d*", raw):
        return float(raw)
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Sous-ensemble YAML : `clé: valeur` et listes `- item`. Retourne (métadonnées, corps)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter manquant (le fichier doit commencer par '---')")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter non terminé (second '---' manquant)") from None
    meta: dict = {}
    key = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s+-\s*(.*)$", raw)
        if m and key is not None:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_scalar(m.group(1)))
            continue
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", raw)
        if not m:
            raise ValueError(f"ligne de frontmatter invalide : {raw!r}")
        key = m.group(1)
        value = _strip_comment(m.group(2))
        meta[key] = _scalar(value) if value.strip() else None
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n") + "\n"
