# BlueWard v3.0 — Ralph Wiggum Development Loop

You are iteratively building BlueWard v3.0 features using the Ralph Wiggum methodology.

## Your Task

Read `prd.json` to understand the full product requirements. Work through each phase and task in order. For each task:

1. **Read the PRD task** — understand acceptance criteria and files to modify
2. **Read existing code** — understand current architecture before changing anything
3. **Implement the feature** — write clean, minimal code
4. **Write tests** — unit tests covering all acceptance criteria
5. **Run tests** — `python3 -m pytest tests/ -v` — ALL must pass (old and new)
6. **Functional test** — `timeout 15 blueward --verbose --no-tray --no-notify` — verify it runs
7. **Commit** — one commit per task with a descriptive message
8. **Update progress** — mark the task complete in `PROGRESS.md`

## Rules

- Do NOT skip tests. Every task requires passing tests.
- Do NOT break existing functionality. All 158+ existing tests must keep passing.
- Do NOT over-engineer. Implement exactly what the acceptance criteria ask for.
- Do NOT add features not in the PRD.
- Read files before modifying them. Understand before changing.
- Keep commits small and focused — one per task.
- If a task is blocked, document why in PROGRESS.md and move to the next task.

## Progress Tracking

Check `PROGRESS.md` to see what's already done. Update it after completing each task.

## Completion

When ALL phases are complete and ALL tests pass:

<promise>ALL_PHASES_COMPLETE</promise>

Only output this when it is genuinely true — all tasks implemented, all tests passing, all acceptance criteria met.

## Current State

Check git log and PROGRESS.md to understand what iteration you're on and what's been done.
