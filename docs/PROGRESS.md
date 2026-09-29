# PROGRESS — orchestrator state (recover from here after any context reset)

Branch: `claude/brave-edison-zewo7e` (all work lands here; no PRs unless the user asks).
Python: `/home/user/.venvs/autoapply/bin/python`, always `PYTHONPATH=src`. Gate: `scripts/gate.sh`.
Workers: Sonnet agents, `isolation: worktree`, merged locally by the orchestrator.
Sandbox facts: ATS hosts + api.openai.com are blocked (403) -> everything is verified against local mocks.

## Wave log
| wave | scope | status |
|---|---|---|
| 0 | spec, contracts, scaffold, mock-ATS base | done |
| 1 | db, platform services, workbook, boards, scoring, tailoring, mock sites (x2) | pending |
| 2 | apply engine, adapters, answers, accounts, generic filler | pending |
| 3 | pipeline, scheduler, dashboard, CLI/PowerShell, LinkedIn/Indeed | pending |
| 4 | acceptance suite + fix loop | pending |

## Acceptance criteria status (SPEC section 9)
A1 - A2 - A3 - A4 - A5 - A6 - A7 - A8 - A9 - A10 - A11 - A12 - A13 - A14 - A15 -   (all open)

## Open items / decisions
- Live-site behaviour cannot be verified from the sandbox; `doctor --live-dry-run` is the user's validation tool.
