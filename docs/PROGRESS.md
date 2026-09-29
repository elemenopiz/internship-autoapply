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
| A | db.py | worktree-agent-aad13fe4da705016e | MERGED (861 tests green) |
| B | secrets/llm/readiness | worktree-agent-a3c3d4d01b46f7f2b | MERGED |
| C | workbook + dedupe + fixtures | w1/workbook | MERGED |
| D | boards + scoring | w1/boards-scoring | MERGED |
| E | tailor + sample_profile | w1/tailor | MERGED |
| F | mock greenhouse/lever/ashby/blockers | w1/mocks-a | MERGED (2931 tests, gate 2.5min) |
| G | mock workday/employer portal | w1/mocks-b | MERGED (2994 tests; gate ~4min) |

## Incident log
- 12:00 UTC: all 8 running workers died together on an API session rate limit (429). Partial work survived in the
  worktrees. Resumed C,D,E,F,G via SendMessage; HELD w2/accounts, w2/dashboard, w2/pipeline (barely started; relaunch
  into the same worktrees after Wave 1 merges). Lesson: keep at most ~5 heavy workers in flight, tell workers to be
  economical (no mutation-testing campaigns).

## Wave 2 workers launched early (deps already merged / injected)
| task | branch / worktree | status |
|---|---|---|
| accounts + emailverify | w2/accounts | resumed, running |
| dashboard | w2/dashboard | running |
| pipeline + scheduler | w2/pipeline | running |
| apply framework (browser/blockers/registry/engine/helpers) | w2/framework | running |
| answers engine | w2/answers | running |

## Wave log
| wave | scope | status |
|---|---|---|
| 0 | spec, contracts, scaffold, mock-ATS base | done |
| 1 | db, platform services, workbook, boards, scoring, tailoring, mock sites (x2) | DONE, all merged |
| 2 | apply engine, adapters, answers, accounts, generic filler | pending |
| 3 | pipeline, scheduler, dashboard, CLI/PowerShell, LinkedIn/Indeed | pending |
| 4 | acceptance suite + fix loop | pending |

## Acceptance criteria status (SPEC section 9)
A1 - A2 - A3 - A4 - A5 - A6 - A7 - A8 - A9 - A10 - A11 - A12 - A13 - A14 - A15 -   (all open)

## Open items / decisions
- Live-site behaviour cannot be verified from the sandbox; `doctor --live-dry-run` is the user's validation tool.

## Backlog for the fix loop (found in review of merged work)
- scheduler: enabling the schedule must NOT trigger an immediate catch-up run (initialise last_slot=now when unset).
- accounts: adapters must pass the RAW hostname (urlsplit(url).hostname), not normalize.host_of, to AccountManager.
- accounts: treat failed sign-in on an unverified never-logged-in account as "register" (crash between password gen and signup).
- pipeline: runner's own LLM use is outside BudgetedLLM; a hung runner is only stopped by the engine's cooperative deadline.
