#!/usr/bin/env bash
# check-version.sh — verify the lope version string is in sync across all 6 locations.
#
# Usage: ./scripts/check-version.sh
#
# Exits 0 if all 6 strings match. Exits 1 with a diff report if they drift.
# Intended for use in release scripts and CI.
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Extract the version string from each file. Each extractor returns the bare
# version (e.g. "0.3.1"), or exits 1 with an error if extraction fails.

v_pyproject="$(grep -E '^version = "' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
v_init="$(grep -E '^__version__ = "' lope/__init__.py | head -1 | sed -E 's/__version__ = "([^"]+)"/\1/')"
v_install="$(grep -E '^echo "Lope v' install | head -1 | sed -E 's/echo "Lope v([^ ]+) .*/\1/')"
v_claude="$(grep -E '"version": "' .claude-plugin/plugin.json | head -1 | sed -E 's/.*"version": "([^"]+)".*/\1/')"
v_cursor="$(grep -E '"version": "' .cursor-plugin/plugin.json | head -1 | sed -E 's/.*"version": "([^"]+)".*/\1/')"
v_gemini="$(grep -E '"version": "' gemini-extension.json | head -1 | sed -E 's/.*"version": "([^"]+)".*/\1/')"

# Report what each file thinks the version is
printf '%-32s %s\n' "pyproject.toml:"            "$v_pyproject"
printf '%-32s %s\n' "lope/__init__.py:"          "$v_init"
printf '%-32s %s\n' "install:"                   "$v_install"
printf '%-32s %s\n' ".claude-plugin/plugin.json:" "$v_claude"
printf '%-32s %s\n' ".cursor-plugin/plugin.json:" "$v_cursor"
printf '%-32s %s\n' "gemini-extension.json:"     "$v_gemini"

# Any empty extraction is a bug in this script or a file format change
for pair in \
  "pyproject.toml:$v_pyproject" \
  "lope/__init__.py:$v_init" \
  "install:$v_install" \
  ".claude-plugin/plugin.json:$v_claude" \
  ".cursor-plugin/plugin.json:$v_cursor" \
  "gemini-extension.json:$v_gemini"
do
  name="${pair%%:*}"
  value="${pair#*:}"
  if [ -z "$value" ]; then
    echo
    echo "ERROR: could not extract version from $name — file format may have changed" >&2
    exit 1
  fi
done

# All must be identical
if [ "$v_pyproject" = "$v_init" ] && \
   [ "$v_pyproject" = "$v_install" ] && \
   [ "$v_pyproject" = "$v_claude" ] && \
   [ "$v_pyproject" = "$v_cursor" ] && \
   [ "$v_pyproject" = "$v_gemini" ]; then
  echo
  echo "✓ All 6 version strings in sync at $v_pyproject"
  exit 0
else
  echo
  echo "✗ Version drift detected — files disagree on the current version." >&2
  echo "  Fix by running: ./scripts/bump-version.sh <canonical_version>" >&2
  exit 1
fi
