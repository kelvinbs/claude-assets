# dev-process

_Created: 2026-06-17. Rewritten: 2026-08-30._

`dev-process` is the fixed state machine that governs how dev work is performed
on this project. It is a goal-accomplishment discipline, not a task tracker.
The plan file it produces is a byproduct; the **thinking order** it enforces
is the point.

---

## 1. Dev UX

### 1.1 Scope — dev work only

- `dev-process` governs **dev work**: design, planning, specs, build, debug,
  deploy, validate. Coding is one slice, not the whole.
- Everything else — questions, ops one-offs, reading, chat — runs outside the
  machine, ungoverned.
- **Entry guard:** "Is this dev work? → enter `dev-process`. Else → normal
  operation." Evaluated before any FSM state runs.

### 1.2 Read first, then explore

The record is the domain documentation. Before exploring, read in this order:

1. **`README.md`** at the repo root — the repository map and the document set.
2. **`docs/00-index.md`** — the document map, the architecture, the
   conventions. Read this before any other document.
3. The document that owns the fact in question, per the map in
   `docs/00-index.md`. One fact lives in one document; the numbered `docs/`
   set is the whole record.

### 1.3 Philosophy

- **Altitude ladder.** Work descends one rung at a time, never skipping:
  **Goal** (what) → **Approach** (how, in general) → **Steps** (how,
  specifically).
- **Get the goal right first.** The goal is pinned and agreed before any "how"
  is entertained.
- **Refine the vision in the approach.** The approach is where the vision is
  sharpened, before any step exists.
- **Deferred planning is the spine.** Planning is prohibited until immediately
  prior to execution. Steps written early are written with the least context
  they will ever have; deferring to the last responsible moment means steps
  are written with maximum information. Steps are an output of context
  review, never a guess.
- **Dual-altitude planning.** Steps are written in view of both the narrow
  task and the project-level perspective, simultaneously. Never purely local
  (misses the system), never purely strategic (too vague to act).
- **Step-with-user, not autonomous.** Two hard user-gates per phase (goal,
  approach). Only the user marks a step complete or failed.

What this kills: big upfront plans that lock decisions made with thin context;
goal-skipping (jumping to "how" before "what" is agreed); vision drift
(approach never pinned, so steps wander).

---

## 2. File organisation

**Table 2.1 — Directories**

| # | Path | Holds |
|---|---|---|
| 1 | `README.md` | repository map, document set |
| 2 | `docs/NN-*.md` | the record — numbered documents, one fact in one document; `00-index.md` is the map |
| 3 | `process/` | this file |
| 4 | `plans/<name>_plan.md` | active plans |
| 5 | `plans/pending/` | parked plans awaiting their turn |
| 6 | `plans/archive/` | finished/dead plans — read-only record |
| 7 | `studies/` | design studies, one folder each; see `studies/README.md` |

- **Every plan filename ends in `_plan.md`.** No exception, in `plans/`,
  `pending/` or `archive/`. A file that does not end in `_plan.md` is not a
  plan.
- A plan directly in `plans/` is active by definition. **Park** = move it to
  `pending/`. **Retire** = move it to `archive/`, only after its terminal
  state.
- **The plan file is the state.** There is no separate state file. The plan
  markdown is the single source of truth for FSM state; a parallel `.json`
  mirror would create two sources of truth and invite drift — the exact
  failure this discipline exists to prevent.

**Table 2.2 — State in the plan file**

| # | State | Where it lives in the plan file |
|---|---|---|
| 1 | step status (`not started` … `complete`) | the Status column of each step row |
| 2 | phase substate (Table 3.4) | derivable from the phase block — has `gN`? `aN`? steps written? |
| 3 | current phase / step | the one phase with un-terminal steps; the one step `in progress` |

The invoker parses the plan to find the current state, makes the single legal
move, and edits the plan back. State is read from and written to the markdown
— nowhere else.

---

## 3. Process

### 3.1 Plan-first gate

- **No plan file → no FSM runs at all.** All execution proceeds from a plan
  written to disk in `plans/`.
- The plan must describe the work at sufficient detail to resume after
  interruption. Implementation proceeds step by step against it.
- A plan step executed without a corresponding written plan entry is a
  violation.

### 3.2 Plan structure

Plans use prefixed dot numbering. Every entry is uniquely identifiable by its
prefix and number alone.

**Table 3.2 — Plan keys**

| # | Key | Meaning | Example |
|---|---|---|---|
| 1 | `g0` | Plan goal | `g0. Build all apps on MBP...` |
| 2 | `a0` | Plan approach | `a0. Portal first, apps in sequence` |
| 3 | `p[n]` | Phase | `p1. Portal` |
| 4 | `g[n]` | Phase goal | `g1. Working splash page on MBP` |
| 5 | `a[n]` | Phase approach | `a1. Spec, implement, validate` |
| 6 | `s[p].[n]` | Step | `s1.2. Implement` |
| 7 | `n[p].[n]` | Note | `n1.1 Discovered X during impl` |

Every plan and every phase has a uniquely-keyed goal and approach. Goals
describe what. Approaches describe how in general. Steps describe how
specifically. Notes record what happened.

**Phase citation:** always reference a phase as "pN — Name" (e.g. "p1 —
Portal"), never number or name alone.

### 3.3 Plan format

```markdown
# Plan Title
_Created: YYYY-MM-DD._

**g0.** Plan goal.
**a0.** Plan approach.

---

- ## p1 — Phase Name
  **g1.** Phase goal.
  **a1.** Phase approach.

  | # | Step | Status |
  |---|---|---|
  | s1.1 | Step description | not started |
  | s1.2 | Step description | not started |

  **Notes:**
  - n1.1 Note text

- ## p2 — Phase Name
  **g2.** Phase goal.
  **a2.** Phase approach.

  | # | Step | Status |
  |---|---|---|
  | s2.1 | Step description | not started |

  **Notes:**
```

### 3.4 Phase creation (per phase)

**Table 3.4 — Phase creation states**

| # | State | Who | Action | Gate to advance |
|---|---|---|---|---|
| 1 | propose-goal | Claude | writes `gN` | — |
| 2 | confirm-goal | User | — | **User confirms** |
| 3 | propose-approach | Claude | writes `aN` | — |
| 4 | confirm-approach | User | — | **User confirms** |
| 5 | context-step | Claude | executes `sN.1` (below) and derives the steps | — |
| 6 | write-steps | Claude | writes `sN.2+`, then runs Table 3.5 per step | loops to next phase |

`sN.1` opens every phase and is always: _"Consider the goal and approach of
this phase and the project, and the project history (notes). Note how this
phase fits into the larger context. Consider this phase scope in relation to
the scope of other phases in this plan. Then write steps to accomplish the
goal via the approach in support of that larger context."_

Steps are never pre-populated before goal and approach are agreed.

### 3.5 Step status (per step)

**Table 3.5 — Step status transitions**

| # | From | Event / guard | To | Who sets |
|---|---|---|---|---|
| 1 | not started | Claude starts work | in progress | Claude |
| 2 | in progress | work done | implemented | Claude |
| 3 | implemented | confirmed working | **complete** | **User only** |
| 4 | implemented | tested, broke | **failed** | **User only** |
| 5 | failed | debug retry (append Note) | in progress | Claude |

- `implemented` = work done, Claude recommends marking complete; awaiting
  user confirmation.
- Terminal: `complete`.

### 3.6 Phase-loop

When an in-progress phase is terminated failed:

1. A note of the failed attempt is appended to the phase Notes.
2. All steps except `sN.1` are deleted.
3. The phase is re-attempted from `sN.1`.

---

## 4. Rules and guardrails

### 4.1 Execution

- No plan = no code. Write the plan first, to disk, before any
  implementation step.
- Verify correct branch before executing.
- Goal agreed, approach agreed, then steps — in that order. Never skip.
- **Write a task's detail only at that task's execution time** — never
  pre-populate. One recursive rule: elaborate one level down, only when you
  reach it.
  - Don't write a phase's steps until the phase executes.
  - Don't write a step's substeps until that step executes.
  - A not-yet-executed task is **named** — one line, enough to stay resumable
    and scope-fenced — not detailed. Pre-written detail rots into stale,
    arbitrary concessions.
  - When detail is written, it is in consideration of the plan goal, plan
    approach, phase goal, phase approach, recent progress, and notes.
- Claude never sets `complete` or `failed` — only the user does.
- Debug activity is recorded in the phase Notes as additive append, one
  entry per debug iteration.
- Active plans are updated in place as steps progress. Plans in `archive/`
  are read-only — do not modify them.

### 4.2 The record

- **Do not create domain files.** No `CONTEXT.md`, `CONTEXT-MAP.md`, or
  `docs/adr/`. Do not suggest creating them. Any skill that would normally
  write them works against the numbered `docs/` set instead, and a change to
  a fact belongs in the one document that owns it.
- **Use the record's vocabulary.** When output names a domain concept — an
  issue title, a proposal, a hypothesis, a test name — use the term as it
  appears in `docs/00-index.md` and the owning document. Do not drift to
  synonyms.
- **Do not invent terms.** If a needed concept is absent from the set, that
  is a signal: either the language is invented and should be reconsidered,
  or there is a real gap. Name the gap; do not fill it.
- **Flag conflicts with the record.** If output contradicts a fact in the
  numbered `docs/` set, surface it explicitly rather than silently
  overriding:

  > _Contradicts `docs/NN-<doc>.md` — but worth reopening because…_
