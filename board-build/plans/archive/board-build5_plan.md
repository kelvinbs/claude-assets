# board-build5 — plan

_Created: 2026-10-05._

**g0.** board-build carries a one-board design over to several boards
without hand work on the record: the project files stay out of the design
folder, rooms follow their parts between pages, a page's sheet follows it
between boards, a new board can start as a whole copy of an existing PCB,
and an unused part can be deleted.

**a0.** Five phases, in the order below. Each phase fixes or adds one thing
in the skills it names, in that skill's own style, and updates those skills'
docs in the same commit. Each change is proven on a scratch copy of the
radar_2 poc1 design before commit.

## Fences — every phase

| # | Fence |
|---|---|
| f1 | Work is limited to the capabilities named in p1–p5 |
| f2 | New behaviour is added as a new verb or a new flag, or as a fix to a verb's stated behaviour. Existing verbs keep their arguments and output |
| f3 | Changes are limited to the SKILL.md and script of the skills a phase names, and the `board-build-tool.md` tables the change touches |
| f4 | Tests run on a scratch copy of `radar_2/builds/poc1/design/` in the session scratchpad. The radar_2 repository itself is not touched |
| f5 | One commit per phase, code and docs together. Push when the User says |
| f6 | board-build is changed only through this plan. At runtime — using board-build on a design — a gap is reported to the User |

## Required reading

**Every word of every item below is read in full immediately prior to phase
execution. Mandatory. No item is skipped, skimmed or assumed known.**

- `dev-process/skills/dev-process/SKILL.md` — the state machine this plan
  runs under: FSM-1 step status, FSM-2 phase creation, phase-loop, and who
  may set `complete` and `failed`
- `board-build/board-build-tool.md` — the tool the work is done on
- `board-build/plans/board-build4_plan.md` — the plan before this one, its
  notes
- `board-build/skills/init-pipeline/SKILL.md` and its script — p1
- `board-build/skills/table-write/SKILL.md` and `table-write.py` — p2, p5
- `board-build/skills/kicad-update/SKILL.md` and `kicad-update.py` — p3, p4

---

## p1 — Init on a split record

**g1.** `init-pipeline` re-run on a record that names two or more boards
writes nothing into the design folder but the record and the shared
library.

**a1.** `init-pipeline` reads `ref_table.board` before it makes the KiCad
project. Two or more boards: the project, root sheet and board files are
not written, and the run says so. The board projects under
`design/kicad files/` are `kicad-update`'s. Skill named: `init-pipeline`.

| # | Step | Status |
|---|---|---|
| s1.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | abandoned |
| s1.2 | `init-pipeline.py`: read the boards `ref_table` names after the tables are made; two or more and the project, root sheet and board files are not made, and the run says so | abandoned |
| s1.3 | `init-pipeline/SKILL.md`, The KiCad project: the split-record rule | abandoned |
| s1.4 | Test on the scratch copy: split record gains `mate_table`, rows kept, no KiCad file in `design/`; a fresh single-board folder still gets all three | abandoned |
| s1.5 | Commit p1, code and docs together | abandoned |

**Notes:**

- n1.1 s1.4 on the scratch copy: run reported the two boards and wrote no project, sheet or board in `design/`; `mate_table` created; ref_table 490, net_table 1044 rows before and after. Fresh folder `one/design`: `one.kicad_pro`, `.kicad_sch`, `.kicad_pcb` written

---

## p2 — Rooms follow their parts

**g2.** A part moved to another page keeps its room structure there, and
the rooms it leaves empty are gone from the old page.

**a2.** `table-write move` rebuilds the part's chain of rooms on the new
page, found or created under the same parents, and drops a room left with
nothing in it. `room --under <name>` resolves a room on the instance's own
page. A new verb drops an empty room by name and page. Skill named:
`table-write`.

| # | Step | Status |
|---|---|---|
| s2.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | abandoned |
| s2.2 | `table-write.py` `move`: the row's room chain found or made on the new page under the same holder; rooms it leaves empty dropped, upward | abandoned |
| s2.3 | `table-write.py`: `room --under <name>` resolves on the instance's page and refuses two of that name; verb `drop-room <name> --page P [--under]` | abandoned |
| s2.4 | Docs: `table-write/SKILL.md` usage, `room`, `drop-room`, `move`, refusals; `board-build-tool.md` T5.1 row 8 | abandoned |
| s2.5 | Test on the scratch copy: move the whole TX tree, AE7 and AE8 to page `TX`; structure before and after compared; `--under` and `drop-room` cases and refusals | abandoned |
| s2.6 | Commit p2, code and docs together | abandoned |

**Notes:**

- n2.1 s2.5: 55 parts moved RF to TX. Every part's room path to its first instance holder identical before and after. Rooms RF 34 to 22, TX 12; no TX room left under a RF-page holder. `room --under "PA A"` refused, 3 on TX; `--under U10` took. `drop-room` refused a non-empty room and an unknown one, dropped empty ones
- n2.2 Seen, present before this plan: page `Compute` holds two empty rooms, `Ethernet` and `CM5`
- n2.3 `/usr/bin/python3` is 3.9 and does not compile `table-write.py`, line 712, an f-string with a backslash. Tests ran on `/opt/homebrew/bin/python3`, 3.14

---

## p3 — A page changes board

**g3.** A page whose board changes in the record takes its sheet file, the
User's wiring with it, into the new board's folder.

**a3.** On the place run, `kicad-update` finds a page file sitting in a
board folder other than its record board's, moves it to the right folder,
and reports it. No second copy is left. Skill named: `kicad-update`.

| # | Step | Status |
|---|---|---|
| s3.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | abandoned |
| s3.2 | `kicad-update.py`: `relocate_pages` on the place run moves a page file into its record board's folder and reports it; two files for one page refused | abandoned |
| s3.3 | `kicad-update.py`: `page_files(record=True)` on the place run, so pages of a board whose root is not yet written are read back. Push keeps the root-only map: the sim fittings walk every sheet file and take only their own root's pages | abandoned |
| s3.4 | Docs: `kicad-update/SKILL.md`, Boards | abandoned |
| s3.5 | Test on the scratch copy: the radar_2 p3 split end to end — init, TX tree to `TX`, TP1–TP4 to `Baseband`, boards by page, place with `--move`, push, place again | abandoned |
| s3.6 | Commit p3, code and docs together | abandoned |

**Notes:**

- n3.1 s3.5: 9 page files moved from `poc1-board-rf/` to `poc1-board-motherboard/` and `poc1-board-rx/`. Push clean, second place reports zeros. Netlist components per board equal the record's: tx 42, rx 69, patch 5, motherboard 230 plus the 4 Buck sheet blocks. Wires on every moved page equal the original but `poc1-rf`, 27 to 19: the 8 gone are port-area stubs at x 807–815 mm, the tool's, fewer nets leaving the page
- n3.2 First try added every record page to `page_files` for every caller; push then failed in the sim fittings, `root sheet has no sheet symbol for page 'Baseband'`. Narrowed to the place run, s3.3
- n3.3 `poc1-board-rf/` keeps its root, project and PCB after the split; its root still names the moved pages. Left for the User

---

## p4 — Board from a whole copy

**g4.** A new board's PCB can start as a whole copy of an existing PCB —
footprints, tracks, zones, drawings — for the User to cut down.

**a4.** A flag on `kicad-update --template` keeps everything on the
template, not only its layers and setup. The project file is written as
`--template` writes it today. Skill named: `kicad-update`.

| # | Step | Status |
|---|---|---|
| s4.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | abandoned |
| s4.2 | `kicad-update.py`: flag `--whole` on `--template` writes the template PCB entire; `--whole` alone refused | abandoned |
| s4.3 | Docs: `kicad-update/SKILL.md` usage and Boards; `board-build-tool.md` T5.1 row 9 | abandoned |
| s4.4 | Test on the p3 scratch split: `tx`, `rx`, `motherboard` from `poc1-board-rf.kicad_pcb` whole; counts against the template; template hash unchanged; refusals; push | abandoned |
| s4.5 | Commit p4, code and docs together | abandoned |

**Notes:**

- n4.1 s4.4: each copy 339 footprints, 2048 segments, 58 zones, 1380 vias, as the template; template hash unchanged. A second `--template tx` refused, the PCB exists; `--whole` alone refused. Push rewrote paths on tx 42, motherboard 4 (TP1–TP4), rx 0: a PCB path omits the root, so a page that only changed board keeps its paths. Each copy reports the other boards' footprints as not in the record

---

## p5 — Delete a part

**g5.** A part no instance uses can be deleted from the record.

**a5.** A new `table-write` verb removes the part's `parts_table` row and
its `price_table` rows. It refuses a part any instance points at. The IPN
is not reused. Library symbol, footprint and part file are reported, not
removed. Skill named: `table-write`.

| # | Step | Status |
|---|---|---|
| s5.1 | Consider the goal and approach of this phase and the project, and the project history (notes). Note how this phase fits into the larger context. Consider this phase scope in relation to the scope of other phases in this plan. Then write steps to accomplish the goal via the approach in support of that larger context. | abandoned |
| s5.2 | `table-write.py`: verb `drop-part <part>`: refuses a part in use, naming the instances; deletes the part and its price rows; reports symbol, footprint, part file left | abandoned |
| s5.3 | `table-write.py` `next_ipn`: the mark is the highest IPN in the record, the part files and the library's `ipn` fields | abandoned |
| s5.4 | Docs: `table-write/SKILL.md` usage, `drop-part`, refusals, Numbering; `board-build-tool.md` T5.1 row 8 | abandoned |
| s5.5 | Test on the scratch copy: drop W0021 while U35 uses it, refused; `change-part U35 W0020`; drop W0021 by name; again, refused; next W part is W0022 | abandoned |
| s5.6 | Commit p5, code and docs together | abandoned |

**Notes:**

- n5.1 s5.5, first run: with the record alone as the mark, the next W part took W0021 again; pcb-features have no part file. The library symbol's `ipn` field added to the mark; second run gave W0022
