#!/usr/bin/env bash
# Fails (exit 1) if any relative link in a tracked Markdown file points to a
# path that does not exist. Only git-tracked files are scanned, so local
# untracked material (.remember/, scratch/, .audit/) can never fail the check.
# External links (http/https/mailto) are not checked. Run from the repo root.
set -uo pipefail

fail=0
while IFS= read -r -d '' md; do
  dir=$(dirname "$md")
  while IFS= read -r target; do
    path="${target%%#*}"
    [ -z "$path" ] && continue
    case "$path" in
      http://*|https://*|mailto:*) continue ;;
      /*) resolved=".$path" ;;
      *) resolved="$dir/$path" ;;
    esac
    if [ ! -e "$resolved" ]; then
      echo "BROKEN: $md -> $target"
      fail=1
    fi
  done < <(grep -oE '\]\([^)]+\)' "$md" | sed -E 's/^\]\(//; s/\)$//')
done < <(git ls-files -z -- '*.md')

if [ "$fail" -eq 0 ]; then
  echo "OK: all relative Markdown links resolve"
fi
exit "$fail"
