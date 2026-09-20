---
name: dev-process
description: Dev-work state machine — the fixed FSM governing how dev work is performed. Reads the single live plan, makes one legal move per turn; state lives in the plan file. Use when starting or continuing planned dev work, or when the user says "dev-process".
---

# dev-process — the Dev-Work State Machine

_Created: 2026-06-21._

---

## What This Is

- **`dev-process`** is the named, **fixed state machine** that governs how dev work is performed on this project.
- It is a **goal-accomplishment discipline**, not a task tracker. The plan file it produces is a byproduct; the **thinking order** it enforces is the point.
- The mechanics live in the Implementation Rules below (the authority). The sections above them **name** that machine, state its **philosophy**, and draw the FSMs explicit so they can be implemented and invoked.

---

## Scope — Dev Work Only

- `dev-process` governs **dev work**: design, planning, specs, build, debug, deploy, validate. Coding is one slice, not the whole.
- Everything else — questions, ops one-offs, reading, chat — runs **outside** the machine, ungoverned.
- **Entry guard:** "Is this dev work? → enter `dev-process`. Else → normal operation." This guard is evaluated before any FSM state runs.

---

## Philosophy

- **Altitude ladder.** Work descends one rung at a time, never skipping:
  - **Goal** (what) → **Approach / vision** (how, in general) → **Steps** (how, specifically).
- **Get the goal right first.** The goal is pinned and agreed before any "how" is entertained.
- **Refine the vision in the approach.** The approach is where the vision is sharpened, before any step exists.
- **Deferred planning is the spine.** Planning is **prohibited until immediately prior to execution**. This is the load-bearing rule, not a side rule.
  - Steps written early are written **dumb** — with the least context they will ever have.
  - Deferring to the last responsible moment means steps are written with **maximum information**.
  - Steps are an **output of context review**, never a guess.
- **Dual-altitude planning.** When steps are finally written, they are written in view of **both** the **narrow task** and the **project-level perspective** — simultaneously. Never purely local (misses the system), never purely strategic (too vague to act).

### What it kills (anti-patterns)

- Big upfront plans that lock decisions made with thin context.
- Goal-skipping — jumping to "how" before "what" is agreed.
- Vision drift — approach never pinned, so steps wander.

---

## FSM-1 — Step Status (per step)

| From | Event / guard | To | Who sets |
|---|---|---|---|
| not started | Claude starts work | in progress | Claude |
| in progress | work done | implemented | Claude |
| implemented | confirmed working | **complete** | **User only** |
| implemented | tested, broke | **failed** | **User only** |
| failed | debug retry (append Note) | in progress | Claude |

- **Terminal:** `complete`.
- **Guard:** Claude may **never** set `complete` or `failed` — only the user does.

---

## FSM-2 — Phase Creation (fixed 6-state sequence, per phase)

| State | Action | Gate to advance |
|---|---|---|
| S0 create | phase block written: title, blank `gN`, blank `aN`, `sN.1` boilerplate. No other content | — |
| S1 propose-goal | Claude writes `gN` (phase goal) **only on the User's request** | — |
| S2 confirm-goal | wait | **User confirms** |
| S3 propose-approach | Claude writes `aN` (phase approach) **only on the User's request** | — |
| S4 confirm-approach | wait | **User confirms** |
| S5 context-step `sN.1` | Claude does the mandatory context review (goal + approach + project) and **derives** the steps | — |
| S6 write-steps | Claude writes `sN.2+`, then runs FSM-1 per step | loops to next phase |

- Two **hard user-gates** (S2, S4). The machine is **step-with-user**, not autonomous.
- At creation a phase holds **only** the `sN.1` boilerplate beside blank `gN` and `aN`. Claude offers no goal and no approach unless asked.
- `sN.1` is always the context step — it is where deferred planning happens, at the edge of execution.
- Steps are **never pre-populated** before goal and approach are agreed.

---

## Wrapping Gate — Plan-First

- **No plan file → no FSM runs at all.** All execution proceeds from a plan written to disk in `plans/`.
- A plan step executed without a corresponding written plan entry is a **violation**.
- **Phase citation:** always reference a phase as "pN — Name" (e.g. "p1 — Portal"), never number or name alone.

### Standard folder layout

```
docs/
plans/
studies_not_requirements/
```

---

## Open Questions (to resolve before implementing)

- **Goal-rightness gate at plan level.** Phase goal gets a user-confirm (S2). Is getting the **plan-level `g0`** right also a guarded state, or assumed upstream?
- **Definition of "immediately prior."** Currently planning is per-phase (`sN.1` writes a whole phase's steps at phase start). Is that "immediate" enough, or do you want **step-level** just-in-time (re-derive before each step)?
- **Approach mutability.** May the **approach** change mid-phase if a step reveals the vision was wrong, or is it frozen once confirmed (S4)?

---

## State Storage — the plan file IS the state (no JSON)

**There is no separate state file.** The plan markdown in `plans/<plan>.md` is the **single source of truth** for FSM state — adding a parallel `.json` mirror would create two sources of truth and invite drift (live ≠ recorded), the exact failure this discipline exists to prevent.

| FSM state | Where it already lives in the plan file |
|---|---|
| FSM-1 step status (`not started`…`complete`) | the **Status column** of each step row |
| FSM-2 phase substate (S1–S6) | derivable from the phase block — has `gN`? `aN`? steps written? |
| current phase / step | the one phase with un-terminal steps; the one step `in progress` |

The invoker **parses** the plan to find the current state, makes the single legal move, and **edits the plan back**. State is read from and written to the markdown — nowhere else.

---

## Implementation Status

- **Definition:** this doc. ✓
- **State:** the plan file (`plans/<plan>.md`) — single source of truth, **no JSON state file** (decided 2026-06-30). ✓ (already how plans are written)
- **Invoker** `/dev-process` skill (parses the active plan → enforces the only legal transition → writes the plan back): **not built**.

---
---

# Implementation Rules

_Created: 2026-06-17_

---

## Plans Drive All Execution

All coding must be performed from a plan written to disk. Executing steps that exist only in memory and not in a plan file on disk is prohibited. No exceptions.

Before any implementation begins:
- A plan file must exist on disk in the project's `./plans/` folder
- The plan must describe the work to be done at sufficient detail to resume after interruption
- Implementation proceeds step by step against the written plan

---

## Plan File Location

All plan files are stored in `plans/`. Plans directly in that folder are active by definition; archived plans live in `plans/archive/`.

---

## Plan Structure

Plans use prefixed dot numbering. Every entry is uniquely identifiable by its prefix and number alone.

| Key | Meaning | Example |
|---|---|---|
| `g0` | Plan goal | `g0. Build all apps on MBP...` |
| `a0` | Plan approach | `a0. Portal first, apps in sequence` |
| `p[n]` | Phase | `p1. Portal` |
| `g[n]` | Phase goal | `g1. Working splash page on MBP` |
| `a[n]` | Phase approach | `a1. Spec, implement, validate` |
| `s[p].[n]` | Step | `s1.2. Implement` |
| `n[p].[n]` | Note | `n1.1 Discovered X during impl` |

Every plan and every phase has both a uniquely-keyed goal and a uniquely-keyed approach. Goals describe what. Approaches describe how in general. Steps describe how specifically. Notes record what happened.

## Plan Format

A plan is created by copying the plugin's `plan-template.md` whole and filling
it in. That file is the boilerplate; this section does not restate it.

Required reading is mandatory in every plan. It lists every document, skill
`SKILL.md` and record the phases touch — listed, not assumed — and every word
of every item is read in full immediately prior to phase execution.

---

## Plan Status

Every step tracks status. Permitted states:

- **not started**
- **in progress**
- **implemented** — work done, Claude recommends marking complete; awaiting user confirmation
- **complete** — confirmed working by user
- **failed** — tested and did not work

Claude sets **implemented**. Only the user sets **complete** or **failed**.

---

## Phase-Loop

**phase-loop means: reset state, do the required reading, execute sN.1.**

- **phase-loop**: the in-progress phase execution is terminated failed
- A note of the failed attempt is added to the phase Notes section
- All steps except sN.1 are deleted
- The plan's Required reading is read again, every word, before sN.1 runs
- The phase is re-attempted from sN.1

---

## Phase Creation Sequence

New phases follow this sequence — no step is skipped:

| Step | Who | Action |
|---|---|---|
| 0 | Claude | Creates the phase block: title, blank `gN`, blank `aN`, `sN.1` boilerplate — nothing else |
| 1 | Claude | Proposes `gN` (phase goal) — on the User's request only |
| 2 | User | Confirms goal |
| 3 | Claude | Proposes `aN` (phase approach) — on the User's request only |
| 4 | User | Confirms approach |
| 5 | Execution begins | `sN.1` is always: "Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context." |
| 6 | Claude | Writes remaining `sN.2+` steps based on that context review |

Steps are never pre-populated before goal and approach are agreed. Goal and approach are never proposed unasked. `sN.1` opens every phase — the mandatory context/planning step (review goals, approach, larger context, scope vs other phases → derive steps).

---

## Rules

- No plan = no code. Write the plan first.
- **Verify correct branch before executing.**
- Plans are written to disk before any implementation step is taken.
- A plan step executed without a corresponding written plan entry is a violation.
- Goal agreed, approach agreed, then steps — in that order. Never skip.
- **Write a task's detail only at that task's execution time** — never pre-populate. One recursive rule: elaborate one level down, only when you reach it.
  - Don't write a **phase's steps** until the **phase** executes.
  - Don't write a **step's substeps** (its execution detail / how) until that **step** executes.
  - A not-yet-executed task is **named** — one line, enough to stay resumable + scope-fenced — **not detailed**. Pre-written detail rots into stale, arbitrary concessions.
  - When detail is written, it's in consideration of the plan goal, plan approach, phase goal, phase approach, recent progress, and notes.
- `sN.1` = context step: review goals, approach, larger context, scope vs other phases → derive remaining steps.
- Debug activity is recorded in the phase Notes section as additive append, one entry per debug iteration.
- Plans in `complete/` and `archive/` are read-only records — do not modify them.
- Active plans are updated in place as steps progress.
- Phase citation: always reference a phase as "pN — Name" (e.g. "p1 — New Arch") — never the number or the name alone.
