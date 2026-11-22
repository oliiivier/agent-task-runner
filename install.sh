#!/usr/bin/env bash
# Installation globale : binaire zipapp ~/.local/bin/agent-runner + symlinks pour les helpers
# (move-lines, file-outline, replace-block) et les skills (~/.claude/skills, ~/.gemini/config/skills
# et, si Codex est installé, ~/.codex/skills : plan-tasks, triage-failed, review-batch).
# Usage : ./install.sh [--uninstall]
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
AGENT_RUNNER_BIN="$BIN_DIR/agent-runner"
CLAUDE_SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
AGY_SKILLS_DIR="${AGY_SKILLS_DIR:-$HOME/.gemini/config/skills}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

SKILLS=(plan-tasks triage-failed review-batch)
LINKS=(
  "$SRC/helpers/move-lines|$BIN_DIR/move-lines"
  "$SRC/helpers/file-outline|$BIN_DIR/file-outline"
  "$SRC/helpers/replace-block|$BIN_DIR/replace-block"
)
for sk in "${SKILLS[@]}"; do
  LINKS+=(
    "$SRC/skills/$sk|$CLAUDE_SKILLS_DIR/$sk"
    "$SRC/skills/$sk|$AGY_SKILLS_DIR/$sk"
  )
done
# Codex est optionnel : on n'installe les skills que s'il a déjà été lancé (dossier de config présent).
# Relancer ./install.sh après avoir installé Codex suffit (installation idempotente).
if [[ -d "$CODEX_HOME" || -L "$CODEX_HOME/skills/plan-tasks" ]]; then
  for sk in "${SKILLS[@]}"; do
    LINKS+=("$SRC/skills/$sk|$CODEX_HOME/skills/$sk")
  done
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ -L "$AGENT_RUNNER_BIN" || -f "$AGENT_RUNNER_BIN" ]]; then
    rm "$AGENT_RUNNER_BIN"; echo "supprimé : $AGENT_RUNNER_BIN"
  fi
  for entry in "${LINKS[@]}"; do
    dst="${entry#*|}"
    if [[ -L "$dst" ]]; then rm "$dst"; echo "supprimé : $dst"; fi
  done
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 requis" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ requis" >&2; exit 1; }
command -v git >/dev/null || { echo "git requis" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$SRC/agent_runner" "$SRC/templates" "$SRC/skills" "$STAGE/"
find "$STAGE" -type d -name '__pycache__' -exec rm -rf {} +

mkdir -p "$BIN_DIR"
if [[ -L "$AGENT_RUNNER_BIN" ]]; then
  rm "$AGENT_RUNNER_BIN"
fi
python3 -m zipapp "$STAGE" -p "/usr/bin/env python3" -m "agent_runner.cli:main" -o "$AGENT_RUNNER_BIN"
chmod +x "$AGENT_RUNNER_BIN"
echo "compilé : $AGENT_RUNNER_BIN"

chmod +x "$SRC/bin/agent-runner" "$SRC/helpers/move-lines" "$SRC/helpers/file-outline" "$SRC/helpers/replace-block"
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
