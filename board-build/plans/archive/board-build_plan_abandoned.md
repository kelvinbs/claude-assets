# Board-build Plan
_Created: 2026-08-19. Rewritten: 2026-08-20. Format: `docs/process/dev-process.md`._
_Abandoned: 2026-08-20. Superseded by plans/board-build2_plan.md._

**g0.** A working board-build tool: the six processes of
`tools/board-build/board-build-tool.md`, run as processes, carrying a board
through KiCad.

**a0.** One phase per capability slice. Tools are validated alone before a
process uses them. Every attempt is recorded as a note. The spec is
User-only; code is written to satisfy it.

## Background

| Item | Value |
|---|---|
| The tool | `board-build` — the framework for agent-assisted hardware design in KiCad. Spec `tools/board-build/board-build-tool.md`, scripts `tools/board-build/tools/` |
| The DUT | `builds/proto2` — the workpiece the tool acts on. Board directory `builds/proto2/design`; every file in it is tool-made |
| The relation | class versus object instance. `board-build` is the general procedure, valid for any board; the DUT is one instance of running it |
| Drift risk | the tool and the DUT are designed alternately. A statement about one gets read as a statement about the other. Different lifetimes, different documents, different owners of truth |
| How the work runs | the tool is made process by process, as the spec numbers them. A process's tools are created and validated alone, then applied with the DUT as the example. Approval of a tool considers its application to the DUT |
| Spec guard | the spec is **User-only**. `.claude/hooks/guard-spec.py` denies agent edits; `guard-toolset.py` denies adding or removing tool scripts. A spec/script disagreement is reported, never resolved by editing the spec |
| History | proto1 was the DUT through 2026-08-19; the DUT is now proto2. Prior state, attempts and lessons: Appendix |

## Required reading

A session working this plan reads, in order:

1. `docs/process/dev-process.md` — the plan format and the state machine this plan runs under.
2. `tools/board-build/board-build-tool.md` — the spec: the six processes and the toolset.
3. This plan, including the Appendix.
4. `builds/proto2/02-bom.md` — the DUT's parts source.

## Phases

Phases are created one at a time per FSM-2 of `docs/process/dev-process.md`:
goal proposed, User confirms; approach proposed, User confirms; then the
context step derives the steps. No phase content is pre-populated here.

- ## p1 — Symbol-only chain
  _Not opened. FSM-2 at S1: goal to be proposed, drawing on the Appendix._

---

## Appendix — prior plan content, condensed
_All content of the 2026-08-19 plan file, compressed. Record only; nothing
here is a live goal, approach, step or status._

### Proto1 era (superseded preamble, 2026-08-19 morning)

| Item | Value |
|---|---|
| DUT then | `builds/proto1`; process 1 reference `builds/proto1/designarchive`; `design` at init state |
| Standing | process 1 (update parts) tools rewritten and DUT re-inited, so process 1 not done; process 2 (update library) next |
| Process 1 tools | `db-init`, `table-write`, `kicad-init` — first two run since rewrite, `table-write` not, the three never as a sequence |
| Toolset | ten tools, T4.1 (a count of eleven was a bug); 8 built; placeholders `footprint-draw`, `kicad-update` |
| Coupling | none — a tool runs another as a command |

### Context at rewrite to proto2 (2026-08-19)

| Item | Value |
|---|---|
| Spec | six processes, T3.1, User-only, guard-hooked (see Background) |
| DUT | `builds/proto2` — parts source `02-bom.md` (Detail, Page, Qty), reference csv `designarchive/parts.csv` |
| Init | `python3 tools/board-build/tools/db-init.py builds/proto2/design --scorch` then `kicad-init.py builds/proto2/design --project proto2` |
| Process 1 then | no command file (`commands/update-parts.md` unwritten); rows driven by hand, one `table-write add` per 02-bom row, then `table-write mpn`. Known-good: 45 parts, 57 instances, 22 approvals |
| Process 2 then | symbol only. Batch JSON `[{"name": IPN, "hint": MPN + description}]`, then `copy-kicad-part.py <board> --batch <file>` — one ask. Draw fallback out of scope for p1 |
| Process 3 then | `kicad-update.py` holds sheet-place's code, never run |
| Test discipline | smoke on 3 parts before any full batch. Errors to the terminal, never `/dev/null`. Every attempt gets a note |

### p1 as previously drafted (goal and approach were awaiting User confirmation — never confirmed)

- g1 draft: processes 1, 2 and 3 end to end on `builds/proto2`, symbol only —
  the record by tool, a stock symbol or a reasoned null per part, a schematic
  the User reviews in KiCad.
- a1 draft: validate each tool alone at smoke scale, then run the processes
  in order. Fix `copy-kicad-part` batch first: a bad pick skips the part,
  the ask chunked so every part is answered.
- Only step was s1.1, the context step; not started.

### Notes carried over

- n1.1 Attempt 100, 2026-08-19: full 44-part batch, one ask — 2 hits, 5 of
  44 answered, aborted at A0006 on a fault, 7 min 14. Errors hidden by
  `2>/dev/null`.
- n1.2 Attempt 101, 2026-08-19: 3-part smoke, one ask — 3 of 3 correct, 74 s.
- n1.3 End of 2026-08-19: a full day, zero task progress. Damage done and
  repaired — spec cut and restored, DUT deleted and rebuilt — plus guard
  hooks added. Repair is not progress. Net new work: the scoring fix
  (verified 3-for-3) and the batched ask (failed at 44).
- n1.4 The loop to break: change, full-batch run, opaque failure, blame,
  re-init, repeat.
- n1.5 `symbol-draw` still calls `copy-kicad-part` per part; moves to
  `--batch` once the batch path is validated.
