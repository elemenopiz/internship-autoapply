#!/usr/bin/env bash
# Quality gate. Usage: scripts/gate.sh [pytest args...]   (default: whole test-suite)
# Formats + lints + type-checks the whole repo, then runs pytest. Exit code != 0 means NOT mergeable.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${AUTOAPPLY_PY:-/home/user/.venvs/autoapply/bin/python}"
export PYTHONPATH=src
"$PY" -m ruff format src tests
"$PY" -m ruff check src tests --fix
"$PY" -m mypy
if [ "$#" -eq 0 ]; then set -- tests; fi
"$PY" -m pytest "$@"
