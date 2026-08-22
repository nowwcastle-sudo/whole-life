#!/usr/bin/env bash
# Verifies that the SHA-256 of each protected artifact's HEAD blob appears in
# the "Approved baseline" table of docs/project-context.md.
#
# Hashes are taken over the stored Git blob (LF line endings), matching the
# rule documented in that table: git show HEAD:<path> | sha256sum. Working-tree
# files are never hashed, so core.autocrlf cannot skew the result.
#
# Usage: check-approved-baseline.sh [path ...]
#   With no arguments, checks the two protected artifacts.
set -euo pipefail

paths=("$@")
if [ ${#paths[@]} -eq 0 ]; then
  paths=(docs/spec/whole-life-v0.md docs/adr/0001-local-subscription-v0.md)
fi

fail=0
for path in "${paths[@]}"; do
  hash=$(git show HEAD:"$path" | sha256sum | cut -d' ' -f1)
  if grep -qi "$hash" docs/project-context.md; then
    echo "OK: $path $hash is listed in the approved baseline"
  else
    echo "MISSING: $path $hash is not in the docs/project-context.md baseline table"
    fail=1
  fi
done
exit "$fail"
