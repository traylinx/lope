#!/usr/bin/env bash
# bump-version.sh — atomically bump the lope version string in all 6 locations.
#
# Usage: ./scripts/bump-version.sh NEW_VERSION
# Example: ./scripts/bump-version.sh 0.3.2
#
# Updates these files in lockstep:
#   1. pyproject.toml                   (version = "X.Y.Z")
#   2. lope/__init__.py                 (__version__ = "X.Y.Z")
#   3. install                          (echo "Lope vX.Y.Z ...")
#   4. .claude-plugin/plugin.json       ("version": "X.Y.Z")
#   5. .cursor-plugin/plugin.json       ("version": "X.Y.Z")
#   6. gemini-extension.json            ("version": "X.Y.Z")
#
# Runs check-version.sh at the end to confirm all 6 match.
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 NEW_VERSION" >&2
  echo "Example: $0 0.3.2" >&2
  exit 1
fi

NEW="$1"

# SemVer-ish validation: MAJOR.MINOR.PATCH with optional -prerelease suffix
if ! echo "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$'; then
  echo "ERROR: '$NEW' is not a valid SemVer version (expected X.Y.Z or X.Y.Z-prerelease)" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Read the current version from pyproject.toml (the canonical source)
CURRENT="$(grep -E '^version = "' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')"

if [ -z "$CURRENT" ]; then
  echo "ERROR: could not read current version from pyproject.toml" >&2
  exit 1
fi

if [ "$CURRENT" = "$NEW" ]; then
  echo "Version is already $NEW — nothing to do."
  exit 0
fi

echo "Bumping: $CURRENT → $NEW"
echo

# Cross-platform sed -i: macOS requires '', Linux requires no arg
if sed --version >/dev/null 2>&1; then
  SED_I=(-i)                 # GNU sed
else
  SED_I=(-i '')              # BSD/macOS sed
fi

sed_escape() {
  # Escape a literal string for use in a sed regex (., /, etc.)
  printf '%s' "$1" | sed -e 's/[][\/.^$*]/\\&/g'
}

CURRENT_ESC="$(sed_escape "$CURRENT")"
NEW_ESC="$NEW"  # NEW is already validated as SemVer, no regex metachars

# 1. pyproject.toml
sed "${SED_I[@]}" -E "s/^version = \"${CURRENT_ESC}\"/version = \"${NEW_ESC}\"/" pyproject.toml
echo "  ✓ pyproject.toml"

# 2. lope/__init__.py
sed "${SED_I[@]}" -E "s/^__version__ = \"${CURRENT_ESC}\"/__version__ = \"${NEW_ESC}\"/" lope/__init__.py
echo "  ✓ lope/__init__.py"

# 3. install (bash banner)
sed "${SED_I[@]}" -E "s/Lope v${CURRENT_ESC} —/Lope v${NEW_ESC} —/" install
echo "  ✓ install"

# 4. .claude-plugin/plugin.json
sed "${SED_I[@]}" -E "s/\"version\": \"${CURRENT_ESC}\"/\"version\": \"${NEW_ESC}\"/" .claude-plugin/plugin.json
echo "  ✓ .claude-plugin/plugin.json"

# 5. .cursor-plugin/plugin.json
sed "${SED_I[@]}" -E "s/\"version\": \"${CURRENT_ESC}\"/\"version\": \"${NEW_ESC}\"/" .cursor-plugin/plugin.json
echo "  ✓ .cursor-plugin/plugin.json"

# 6. gemini-extension.json
sed "${SED_I[@]}" -E "s/\"version\": \"${CURRENT_ESC}\"/\"version\": \"${NEW_ESC}\"/" gemini-extension.json
echo "  ✓ gemini-extension.json"

echo
echo "Verifying sync..."
if ! "$REPO_ROOT/scripts/check-version.sh"; then
  echo
  echo "ERROR: version sync check failed after bump. Fix the drifted file manually." >&2
  exit 1
fi

echo
echo "Next steps:"
echo "  1. Add a CHANGELOG entry at the top of CHANGELOG.md: ## $NEW — <tagline>"
echo "  2. Smoke test:  PYTHONPATH=. python3 -m lope version"
echo "  3. Smoke test:  ./install"
echo "  4. Stage, commit, tag, push per docs/RELEASING.md"
