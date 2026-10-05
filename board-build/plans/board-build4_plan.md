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
| s2.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | implemented |
| s2.2 | `table-write.py`: verb `move <ref> --page P`. Every row of the reference takes the new page and the new page's board, and its place is cleared. A parent that is a room leaves with the old page: the instance takes the room's own parent. Reference, ids, symbol UUID and nets kept. Refused for a reference that names no instance, a class-B sheet instance, a drawing in a sub-sheet, a sub-sheet page as target, and the page it is already on | implemented |
| s2.3 | `kicad-update.py`: flag `--move` on the place run. A symbol found on a page other than its record page is removed from that page's file, its uuid then counting as not placed, so the run draws it on its record page. Without the flag the run reports it as today | implemented |
| s2.4 | Docs: `table-write/SKILL.md` usage, a `move` section, refusals; `kicad-update/SKILL.md` usage, the return-direction row, a `--move` note that wires to the old place are left dangling and a footprint on the old board's PCB is reported there; `board-build-tool.md` T5.1 rows 8 and 9 | implemented |
| s2.5 | Test on the scratch copy: move a part to another page on the same board, `kicad-update --move`, then `--push`; check the symbol left the old file, is drawn on the new page with its reference and uuid, its labels follow, a second run reports zeros. Each refusal once | implemented |
| s2.6 | Commit p2, code and docs together. Push | implemented |

**Notes:**

- n2.1 s2.5 on the scratch copy: R1 moved MCU -> Motion, board rf, place cleared; place run without `--move` reported it, with `--move` it left `poc1-mcu.kicad_sch` and was drawn on `poc1-motion.kicad_sch`, same uuid; push wrote its MCU_NRST and 3V3 labels there; a rerun placed nothing and moved nothing. Refused: already on the page, unknown reference, sub-sheet instance SH1, drawing U24 in sub-sheet Buck, target Buck. RX_Block, a block parent with no symbol, moved as a row

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
| s3.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | implemented |
| s3.2 | `table-write.py`: verb `change-part <ref> <part>`, the part named as IPN, name or MPN. Every row of the drawing — every unit, every sub-sheet instance sharing its symbol uuid — takes the target IPN; nothing else in those rows changes. Refused when the reference names no instance, the part names no row, the part is the one it has, either part has no symbol, or the two symbols' pins differ. Pins are read from the project library `lib/<project>.kicad_sym`, pin number to unit, and must match exactly | implemented |
| s3.3 | `kicad-update.py --push`: a placed symbol whose `lib_id` differs from its record part's symbol takes the record's `lib_id`, and the page's `lib_symbols` gains that symbol's definition when it lacks it. Then the existing field push writes Value, Footprint and the rest, and the labels are drawn at the new symbol's pin ends | implemented |
| s3.4 | Docs: `table-write/SKILL.md` usage, a `change-part` section, the refusals, and the opening line on what it reads — the project library's pins for `change-part`, read only; `kicad-update/SKILL.md` push row names the `lib_id`; `board-build-tool.md` T5.1 row 8 | implemented |
| s3.5 | Test on the scratch copy: change a 0402 resistor's value, and a capacitor from 0402 to 0603 or another package the record holds; push; check the sheet symbol keeps uuid, reference and position, takes the new `lib_id`, Value and Footprint, the page carries the new definition, labels sit on the pins, the PCB footprint keeps its path. The footprint swap in the PCB is KiCad's Update PCB from Schematic, a GUI action; it is listed for the User to confirm in KiCad. Each refusal once | implemented |
| s3.6 | Commit p3, code and docs together. Push | implemented |

**Notes:**

- n3.1 s3.5 on the scratch copy: R1 R0001 10 kOhm -> R0003 10 Ohm, same 0402 footprint; C3 C0002 100 nF 0402 -> C0003 10 uF 1206. Rows kept their ids, symbol uuids and places. After push both symbols kept uuid, reference and position, took the new `lib_id`, Value, Footprint and `ipn`, and each page carries the new definition; C3's two labels sit on its pins; R1's PCB footprint path is unchanged. Refused: unknown reference, R9999, the part it already is, R0001 -> A0006 (pins 3–8 differ)
- n3.2 First push left the old `ipn` field on a changed symbol: `ipn` is written at placement, not on push. Fixed: push writes `ipn` when the sheet's differs from the record's
- n3.3 Every push reports one instance updated on one sheet, with or without this phase's code (three pushes of an untouched poc1 copy before p3). Present before this plan; left as found
- n3.4 The footprint swap inside the PCB is KiCad's Update PCB from Schematic, a GUI action, not run here. For the User to confirm in KiCad: the footprint keeps reference, position and rotation, and swaps when it differs

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
| s4.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | implemented |
| s4.2 | Decided: `kicad-update`, because it names and makes the board folders (`<project>-board-<board>` under `design/kicad files/`). Flag `--template BOARD PCB`. Writes `<stem>.kicad_pcb` in the board's folder from the template's header, general, paper, title block, layers and setup — the stackup lives in setup — and nothing else: no footprint, track, via, zone or drawing. The template project's `board` and `net_settings` go into the board's `.kicad_pro`, written then if absent; its `.kicad_dru` is copied beside when present. Normalized by `kicad-cli pcb upgrade`. Refused when the record names fewer than two boards, names no such board, the template is not a file, or the board's PCB exists | implemented |
| s4.3 | Docs: `kicad-update/SKILL.md` usage and a `--template` note in Boards; `board-build-tool.md` T5.1 row 9 | implemented |
| s4.4 | Test on the scratch copy: put a page on a third board, `--template` it from the RF board's PCB; check the PCB holds layers and the stackup and no footprint, track, via or zone; the project file carries the RF board's design settings; a place run then writes the board's sheets and leaves the PCB alone. Each refusal once | implemented |
| s4.5 | Commit p4, code and docs together. Push | implemented |

**Notes:**

- n4.1 s4.4 on the scratch copy: page Motion put on a third board, tx; `--template tx` from the RF board's PCB wrote `poc1-board-tx.kicad_pcb` with version, general, paper, layers, setup and the stackup, and no footprint, segment, via, zone or drawing; its project file took the RF project's `board` design settings and `net_settings`. A place run then wrote the tx root and its Motion page and left the PCB byte for byte; push reported 0 footprints there. Refused: board tx before the record named it, rf whose PCB exists, a template that is not a file, tx a second time, a record with no boards

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
