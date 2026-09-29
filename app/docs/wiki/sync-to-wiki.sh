#!/bin/bash
# Sync docs/wiki/ to the GitHub Wiki repository.
#
# Prerequisites: Initialize the wiki by creating one page at:
#   https://github.com/AbhishekMandapmalvi/AutoApply/wiki/_new
#
# Usage: bash docs/wiki/sync-to-wiki.sh

set -euo pipefail

REPO_URL="https://github.com/AbhishekMandapmalvi/AutoApply.wiki.git"
WIKI_DIR=$(mktemp -d)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Cloning wiki repo..."
git clone "$REPO_URL" "$WIKI_DIR"

echo "Copying wiki pages..."
cp "$SCRIPT_DIR"/*.md "$WIKI_DIR/"

cd "$WIKI_DIR"
git add -A
if git diff --cached --quiet; then
    echo "No changes to push."
else
    git commit -m "Sync wiki from docs/wiki/"
    git push origin master
    echo "Wiki updated successfully."
fi

rm -rf "$WIKI_DIR"
