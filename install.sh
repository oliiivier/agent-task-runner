#!/usr/bin/env bash
# Installation globale par symlinks : ~/.local/bin (agent-runner, move-lines),
# ~/.claude/skills, ~/.gemini/config/skills et, si Codex est installé, ~/.codex/skills
# (skill plan-tasks, commun à tous les runners).
# Usage : ./install.sh [--uninstall]
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CLAUDE_SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
AGY_SKILLS_DIR="${AGY_SKILLS_DIR:-$HOME/.gemini/config/skills}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

LINKS=(
  "$SRC/bin/agent-runner|$BIN_DIR/agent-runner"
  "$SRC/helpers/move-lines|$BIN_DIR/move-lines"
  "$SRC/skills/plan-tasks|$CLAUDE_SKILLS_DIR/plan-tasks"
  "$SRC/skills/plan-tasks|$AGY_SKILLS_DIR/plan-tasks"
)
# Codex est optionnel : on n'installe la skill que s'il a déjà été lancé (dossier de config présent).
# Relancer ./install.sh après avoir installé Codex suffit (installation idempotente).
if [[ -d "$CODEX_HOME" || -L "$CODEX_HOME/skills/plan-tasks" ]]; then
  LINKS+=("$SRC/skills/plan-tasks|$CODEX_HOME/skills/plan-tasks")
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  for entry in "${LINKS[@]}"; do
    dst="${entry#*|}"
    if [[ -L "$dst" ]]; then rm "$dst"; echo "supprimé : $dst"; fi
  done
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 requis" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ requis" >&2; exit 1; }
command -v git >/dev/null || { echo "git requis" >&2; exit 1; }

chmod +x "$SRC/bin/agent-runner" "$SRC/helpers/move-lines"
for entry in "${LINKS[@]}"; do
  target="${entry%%|*}" dst="${entry#*|}"
  mkdir -p "$(dirname "$dst")"
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    echo "ignoré : $dst existe et n'est pas un symlink" >&2; continue
  fi
  ln -sfn "$target" "$dst"
  echo "lié : $dst -> $target"
done

case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Attention : $BIN_DIR n'est pas dans le PATH." >&2 ;; esac
