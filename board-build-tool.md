# board-build

board-build takes a part that has a datasheet and produces a fab package.
It is pointed at a database and a set of intents, and it emits the
schematic, the board and the outputs. Nothing in it is specific to one
product.

It is carried out by an agent. It is deterministic where it can be and
inferential where it must be, and it says which is which at every step.

## Contents

1. [Introduction](#1--introduction)
2. [Assets](#2--assets)
3. [Processes](#3--processes)
4. [Tools](#4--tools)

## 1 — Introduction

**T1.1 — What it covers**

| | |
|---|---|
| Starts at | a part that has a datasheet and a record row |
| Ends at | gerbers, drill, centroid, BOM, ready for a fab |
| Holds | tools, their documents, the schema they read |

**T1.2 — The flow**

| # | |
|---|---|
| 1 | Define a part |
| 2 | Place it |
| 3 | Render |
| 4 | Verify |
| 5 | Fabricate |
| 6 | Source |

**T1.3 — The schema**

| Table | Row | Owner |
|---|---|---|
| `part` | One per IPN — function, requirement, package, pins, pitch, footprint, symbol, datasheet, pins_checked, origin | Hand |
| `aml` | One per IPN and MPN — manufacturer, rank, approved, note | Hand |
| `sourcing` | One per MPN — distributor, part number, price, stock, lifecycle, library tier, priced_on | Generated |

**T1.4 — What the schematic carries**

| Field | Built-in | Holds |
|---|---|---|
| `Reference` | Yes | U3, C12 |
| `Value` | Yes | What it is on the page |
| `Footprint` | Yes | The land pattern |
| `IPN` | No | The join key into the database |

Four. Everything else is joined from the database at output time.

**T1.5 — Placement fields**

| Field | Holds |
|---|---|
| `sheet_room` | The page a part is drawn on |
| `sheet_x`, `sheet_y` | Its position on that page |
| `board_room` | The region of the board it sits inside |
| `board_x`, `board_y` | Its position within that region |

## 2 — Assets

**T2.1 — What constitutes a board project**

| Asset | Kind | Owner |
|---|---|---|
| `parts.db` | Database — part, aml, sourcing | Hand, hand, generated |
| `lib/*.kicad_sym` | Symbols | Hand |
| `lib/*.pretty` | Footprints | Hand |
| `lib/3d/` | Models | Hand |
| `datasheets/` | Source documents | Hand |
| `*.kicad_pro` | Project | Generated once |
| `*.kicad_sch` | Root sheet and pages | Updated |
| `*.kicad_pcb` | Board | Updated |
| `board-setup` | Stackup, fab rules, DRC rule set | Hand |
| `out/netlist` | Intermediate | Generated |
| `out/gerber`, `out/drill`, `out/centroid` | Fab package | Generated |
| `out/bom` | Order table | Generated |
| `out/erc`, `out/drc` | Reports | Generated |

**T2.2 — The clone**

The file organisation exists for one reason: a clone into an empty
directory, on a fresh KiCad install, opens and edits with nothing
missing. No library, footprint or model resolves outside the repository.

| | |
|---|---|
| Library tables | `sym-lib-table` and `fp-lib-table` sit in the project directory and are committed |
| Paths in them | `${KIPRJMOD}/lib/...` |
| Model paths in each `.kicad_mod` | `${KIPRJMOD}/lib/3d/...` |
| Nicknames | Prefixed to the project, so a global entry on another machine cannot collide |
| Other path variables | None. No `${KICAD9_SYMBOL_DIR}` or its kind, no absolute paths, anywhere |
| Symbols, footprints, models | Copied into `lib/`, then owned |

The check script fails the build on any of those, and is run against a
fresh clone rather than the working copy.

## 3 — Processes

**T3.1 — What each one does**

| Process | Does |
|---|---|
| Define parts | Reads a datasheet, writes a `part` row and its symbol, footprint, 3D |
| Update schematic | Writes every page from the database, placing new parts into their sheet room |
| Update board | Writes the board, placing new parts into their board room |
| Verify | ERC, DRC |
| Output | Gerber, drill, centroid, BOM |
| Source | Joins MPNs to distributor APIs |

After a run the file and the database name the same set of parts. The
database owns what a part is; the file owns where it sits.

## 4 — Tools

A tool is a document and a set of Python scripts. The agent follows the
document and calls the scripts; it writes code only to cover a gap in
them, and declares what it wrote so the gap can be closed.

Each tool document opens with the assets it reads and the assets it
writes. The container names no sub-step.

**T4.1 — Layout**

| Path | Holds |
|---|---|
| `board-build-tool.md` | This container |
| `define-parts.md` | One document per tool |
| `update-sch.md` | |
| `update-pcb.md` | |
| `scripts/` | Shared, called by any tool |

**T4.2 — State**

| | |
|---|---|
| Written | This document |
| Built | Nothing. `builds/proto1/tools` is the working precedent and is not yet moved here |
