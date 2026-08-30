# Project template

A human-in-the-loop development/problem-solving process.

## How to use

**Define**

| # | Step |
|---|---|
| 1 | Use the `docs/` file templates (just a suggestion) to define what is being done. |
| 2 | (optional) In `docs/04-approach.md`, break the project into 3–5 parts. Each part is a plan. |

**Plan**

Don't let Claude add slop here. Short goals. Approaches as simple bulleted lists of tasks and guidance. Less is more.

| # | Step |
|---|---|
| 3 | Copy `plans/plan_template.md` and write a concise plan goal (`g0`) and approach (`a0`). |
| 4 | Create phase framework in plan file per the `a0` approach. |

**Execute**

For each phase:

| # | Step |
|---|---|
| 5 | Make or edit the phase goal (e.g. `g1`) and approach (`a1`). |
| 6 | Execute the phase (boilerplate step 1 generates new steps and executes). |
| 7 | (optional) Discuss the results with Claude. Append your prompts with "do not execute": his propensity to act can make a huge mess here. Don't let him modify anything except during a phase loop. |
| 8 | Tell Claude to record the results as a phase-note. |
| 9 | Phase loop to 6 (steps are erased and regenerated from the `sN.1` boilerplate). |

## Layout

| Path | Holds |
|---|---|
| `docs/` | The intent. Several numbered files (`00-index.md`, `01-…`, `02-…`) that describe what is being built and why. This is the main source of project context — read `docs/00-index.md` first. |
| `plans/` | Records of the effort to create the thing described in `docs/`. Active plans sit directly here; `pending/` holds parked plans, `archive/` holds finished or dead ones. |
| `process/` | How work is done. Mainly `dev-process.md`, which explains the development flow, the plan format, and the rules that govern execution. |
| `studies/` | What-ifs and results of side discussions. Material that motivates `docs/` content, or that was used to non-select an approach — relevant, but not the intent. |
