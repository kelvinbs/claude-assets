# board-build4 — plan

_Created: 2026-10-05._

**g0.** board-build is upgraded with edits a KiCad designer makes every day:
rename and delete a net, move an instance to another page or board, point
an instance at another existing part with its reference and layout place
kept, start a board from a template PCB, and record and check the pins that
mate between boards.

**a0.** Five phases, in the order below. Each phase adds a new verb or flag
to the skills it names, in that skill's own style, and updates those skills'
docs in the same commit. Each change is proven on a scratch copy of the
radar poc1 design before commit.

## Fences — every phase

| # | Fence |
|---|---|
| f1 | Work is limited to the capabilities named in p1–p5 |
| f2 | New behaviour is added as a new verb or a new flag. Existing verbs keep their behaviour, arguments and output |
| f3 | Changes are limited to the SKILL.md and script of the skills a phase names, and the `board-build-tool.md` tables the change touches |
| f4 | Tests run on a scratch copy of `radar/builds/poc1/design/` in the session scratchpad |
| f5 | One commit per phase, code and docs together. Push when the User says |
| f6 | board-build is changed only through this plan. At runtime — using board-build on a design — a gap is reported to the User |

## Required reading

**Every word of every item below is read in full immediately prior to phase
execution. Mandatory. No item is skipped, skimmed or assumed known.**

- `dev-process/skills/dev-process/SKILL.md` — the state machine this plan
  runs under: FSM-1 step status, FSM-2 phase creation, phase-loop, and who
  may set `complete` and `failed`
- `board-build/board-build-tool.md` — the tool the work is done on
- `board-build/skills/table-write/SKILL.md` and `table-write.py` — p1, p2,
  p3, p5
- `board-build/skills/kicad-update/SKILL.md` and `kicad-update.py` — p2, p3,
  p4
- `board-build/skills/init-pipeline/SKILL.md` and its script — p4, p5

---

## p1 — Net edits

**g1.** Rename a net everywhere it appears, and delete a net, with one
command each.

**a1.** Two new `table-write` verbs over `net_table`, in the style of `net`.
A rename onto a net name already in use is refused unless a merge flag is
given. `bus_table` and `sim_net_table` take the new name. Sheet labels
follow on the next `kicad-update --push`, as they do for `net` today.

| # | Step | Status |
|---|---|---|
| s1.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | implemented |
| s1.2 | `table-write.py`: verb `rename-net <old> <new> [--merge]` — every `net_table`, `bus_table` and `sim_net_table` row naming `<old>` takes `<new>`. Refused when `<old>` is named nowhere, and when `<new>` is already in use without `--merge`. With `--merge`, a row whose key would collide keeps the row already under `<new>` and drops the `<old>` row. Verb `drop-net <name>` — every row naming the net leaves the three tables. Refused when the net is named nowhere. Both print what changed per table. Usage list in the module docstring | implemented |
| s1.3 | `table-write/SKILL.md`: the two verbs in the usage block, a section each, the refusals in "What it refuses". `board-build-tool.md` T5.1 row 8: function and out columns name the net edits and the tables written | implemented |
| s1.4 | Test on a scratch copy of `radar/builds/poc1/design/`: rename, rename onto a used name refused, rename with `--merge`, drop, both refusals on an unknown net, then `kicad-update --push` and check the sheet labels carry the new name and the dropped net's labels are gone | implemented |
| s1.5 | Commit p1, code and docs together. Push | implemented |

**Notes:**

- n1.1 s1.4 on a scratch copy of poc1: GNSSA_TX renamed in `bus_table`; rename onto 3V3 refused; NOPE refused; BB_I renamed on 3 pins and its simulation source; GNSSA_RX merged into GTX, its bus row dropped and GTX's kept; 5V0_BB merged into 5V0, 28 pins; drop GTX and the refusal on a second drop. `kicad-update --push`: SW_NODE and BB_IX labels on the sheets, no SW, 3V3_CLK, 5V0_BB or BB_I label left. Foreign keys clean

---

## p2 — Instance moves

**g2.** Move an existing instance to another page, and so to that page's
board, with its reference, nets and identity kept.

**a2.** A new `table-write` verb sets the instance's new page and clears its
place. Board follows page, by the existing one-board-per-page rule. A new
`kicad-update` flag moves the symbol from its old sheet to its new sheet,
with its pin labels, and the packer lays it there. Skills named:
`table-write`, `kicad-update`.

| # | Step | Status |
|---|---|---|
| s2.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | not started |

**Notes:**

---

## p3 — Part change

**g3.** Point an existing instance at another existing part — a resistor
value, or a capacitor from 0402 to 0603 — with its reference designator
unchanged and its place in the layout kept.

**a3.** A new `table-write` verb changes `ref_table.ipn` on the instance's
existing row:
- The reference, row, id, symbol UUID, nets, parent and room are kept.
- The target IPN must already exist in `parts_table`.
- The target part must have the same pins as the current part. Nets stay on
  their pin numbers.
- `kicad-update --push` writes the new part's fields, Footprint among them,
  to the symbol. KiCad's Update PCB from Schematic then keeps the footprint
  when it is unchanged and swaps it in place, same reference, position and
  rotation, when it differs, matched by the kept symbol UUID. s3.1 proves
  this on the scratch copy. Skills named: `table-write`, `kicad-update`.

| # | Step | Status |
|---|---|---|
| s3.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | not started |

**Notes:**

---

## p4 — Board from template

**g4.** Start a new board's KiCad project from an existing PCB used as a
template, keeping its stackup, design rules and board setup.

**a4.** A new verb, in `init-pipeline` or `kicad-update` as s4.1 decides,
copies a named template PCB into the new board's folder under
`design/kicad files/`, named for the board. The board's footprints arrive on
its next `kicad-update` run and KiCad's Update PCB from Schematic. Skills
named: `init-pipeline`, `kicad-update`.

| # | Step | Status |
|---|---|---|
| s4.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | not started |

**Notes:**

---

## p5 — Interconnects and cross-board check

**g5.** Record which connector pins mate between boards, coax included, and
check that each mated pair carries the same signal on both sides.

**a5.** A new table in `board.db` holds mating pairs: board A reference and
pin to board B reference and pin. `init-pipeline`'s existing rebuild carries
the schema. New `table-write` verbs add and drop a pair. A new check reads
the record's nets — the nets set with `table-write net` — and reports a pair
whose two pins carry different nets or an unconnected pin. Skills named:
`init-pipeline`, `table-write`.

| # | Step | Status |
|---|---|---|
| s5.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | not started |

**Notes:**
