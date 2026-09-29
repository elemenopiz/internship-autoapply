# PROGRESS — orchestrator state (recover from here after any context reset)

Branch: `claude/brave-edison-zewo7e` (all work lands here; no PRs unless the user asks).
Python: `/home/user/.venvs/autoapply/bin/python`, always `PYTHONPATH=src`. Gate: `scripts/gate.sh`.
Workers: Sonnet agents, `isolation: worktree`, merged locally by the orchestrator.
Sandbox facts: ATS hosts + api.openai.com are blocked (403) -> everything is verified against local mocks.

## Orchestration mechanics (learned the hard way)
- `Agent(isolation="worktree")` bases the worktree on `origin/main` (README only!), NOT on this branch. Workers then must
  merge my branch themselves, which the permission classifier may deny. DO NOT use harness worktrees for new waves.
- Instead: pre-create worktrees from the current tip and launch workers WITHOUT `isolation`:
  `git worktree add .claude/worktrees/<wave>-<name> -b <wave>/<name> claude/brave-edison-zewo7e`
  and give each worker `WORKSPACE: <abs path>` (cd there first, never touch the orchestrator checkout, no ref-changing git).
- Merge: `git merge --no-ff <wave>/<name>` in the main checkout; then `scripts/gate.sh`; then remove the worktree.
- Never `cd` the orchestrator shell into `.claude/worktrees` (it changes the session's primary working dir).
- Workers must not be asked to bypass a permission denial; re-provision them instead.

## Wave 1 workers (branches)
| id | task | branch / worktree | status |
|---|---|---|---|
| A | db.py | worktree-agent-aad13fe4da705016e | running |
| B | secrets/llm/readiness | worktree-agent-a3c3d4d01b46f7f2b | running |
| C | workbook + dedupe + fixtures | w1/workbook | running |
| D | boards + scoring | w1/boards-scoring | running |
| E | tailor + sample_profile | w1/tailor | running |
| F | mock greenhouse/lever/ashby/blockers | w1/mocks-a | running |
| G | mock workday/employer portal | w1/mocks-b | running |

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
