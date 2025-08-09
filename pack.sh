#!/usr/bin/env bash
# pack.sh — create a zip of the current directory with sane excludes + placeholder dirs
# Usage:
#   ./pack.sh                # creates ./TheTower.zip from .
#   ./pack.sh /path/out.zip  # custom output path
#
# Behavior:
# - Excludes .venv, .git, __pycache__, screenshots/matches, logs contents, old/new (entirely)
# - Includes an empty 'logs/' directory as a placeholder (if it exists)

set -euo pipefail

OUT="${1:-./TheTower.zip}"
ROOT="."

# Directories we want as placeholders (directory entry included even if empty)
PLACEHOLDER_DIRS=(
  "./logs"
)

# Exclusion patterns (affect file selection)
# - logs/* => exclude files in logs, but we'll manually add the logs/ dir itself later
EXCLUDES=(
  "./.venv/*" "./*.venv*"
  "./.git/*"
  "./__pycache__/*" "./*/__pycache__/*"
  "./screenshots/matches/*"
  "./logs/*"
  "./old/*" "./new/*"   # exclude contents
  "./old"   "./new"     # exclude the directory entries entirely
)

mkdir -p "$(dirname "$OUT")"
cd "$ROOT"

# Build the file list
FIND_ARGS=(. -type f)
for pat in "${EXCLUDES[@]}"; do
  FIND_ARGS+=( ! -path "$pat" )
done

rm -f "$OUT"

# Collect files and placeholder dirs (avoid dupes)
# Note: newline-delimited paths are fine for zip -@
{
  # 1) All *files* except excluded paths
set -x
  find "${FIND_ARGS[@]}"

  # 2) Explicitly add placeholder directories (directory entries only)
  for d in "${PLACEHOLDER_DIRS[@]}"; do
    if [ -d "$d" ]; then
      printf '%s\n' "$d"
    fi
  done
} | sort -u | zip -q -@ "$OUT"

echo "Wrote: $OUT"


