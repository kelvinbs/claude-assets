# Board-build

A framework for agent-assisted hardware design. It holds the processes that
carry a board through KiCad, the documents that describe them, and the
scripts they call. Nothing in it is specific to one product.

The board is not specified in advance. It is designed by working the
processes in order, and revised by re-entering them — a part is added, a
specification changes, the affected processes run again. The tool is
entered at whichever step is next, not run end to end.

Design choices are made with the agent's assistance. Availability and price
come from the JLCPCB API, which is the only distributor interface that
answers, or from distributor tables the User downloads and hands over.

## Contents

1. [Introduction](#1--introduction)
2. [Assets](#2--assets)
3. [Processes](#3--processes)
4. [Tools](#4--tools)

## 1 — Introduction

**T1.1 — Scope**

| | |
|---|---|
| Covers | The KiCad design process, from the parts table to the fabrication package |
| Entered | At any process, as often as the design needs |
| Excludes | Connectivity. Nets are drawn in the schematic editor and the tool never reads or writes a wire |
| Excludes | Routing. The board's copper is the User's |

**T1.2 — Where the tool works and where the User does**

| Step | Whose |
|---|---|
| Parts table | Tool, with the User's decisions |
| Symbols, footprints, models | Tool |
| Placing symbols on their page | Tool |
| Wiring | User |
| Footprint assignment | Tool |
| Board outline, stackup | User |
| Placing footprints in their region | Tool |
| Routing | User |
| ERC, DRC | Tool runs them and reports |
| Gerbers, drill, centroid, BOM | Tool |

**T1.3 — The parts table**

The design begins here, and every later process reads it. It carries what a
part is, and it is the input a new part is entered into.

| Holds | |
|---|---|
| Identity | Part number, manufacturer, function |
| Physical | Package, pins, pitch |
| Library | Symbol, footprint, model, pins checked |
| Placement | The page a symbol goes on, the region a footprint goes in |
| Source | Where the part comes from, and the alternates that may replace it |

Market data — price, stock, lifecycle — is fetched, never typed, and is
held apart from the fields above so it can be discarded and fetched again.

**T1.4 — What the schematic carries**

| Field | Built in |
|---|---|
| `Reference` | Yes |
| `Value` | Yes |
| `Footprint` | Yes |
| The key back to the parts table | No |

## 2 — Assets

**T2.1 — What constitutes a board project**

| Asset | Owner |
|---|---|
| The parts table | Hand, except the market columns |
| `lib/*.kicad_sym` | Hand |
| `lib/*.pretty` | Hand |
| `lib/3d/` | Hand |
| `datasheets/` | Hand |
| `*.kicad_pro` | Generated once |
| `*.kicad_sch` | Updated by the tool, wired by the User |
| `*.kicad_pcb` | Updated by the tool, routed by the User |
| Board setup — stackup, fabricator rules, DRC rules | Hand |
| `out/` — netlist, gerbers, drill, centroid, BOM, reports | Generated |

**T2.2 — The clone**

The file organisation exists for one reason. A clone into an empty
directory, on a fresh KiCad install, must open and edit with nothing
missing. No library, footprint or model resolves outside the repository.

| | |
|---|---|
| Library tables | `sym-lib-table` and `fp-lib-table` sit in the project directory and are committed |
| Paths in them | `${KIPRJMOD}/lib/...` |
| Model paths in each `.kicad_mod` | `${KIPRJMOD}/lib/3d/...` |
| Nicknames | Prefixed to the project, so a global entry on another machine cannot collide |
| Other path variables | None. No `${KICAD9_SYMBOL_DIR}` or its equivalents, and no absolute paths |
| Symbols, footprints, models | Copied into `lib/`, and owned from that point |

The check runs against a fresh clone rather than the working copy, and
fails on any of the above.

## 3 — Processes

**T3.1 — In order**

| # | Process | Reads | Writes | User then |
|---|---|---|---|---|
| 1 | Define parts | Datasheet | Table row<br>Symbol<br>Footprint<br>Model | — |
| 2 | Update schematic | Table | Symbols, on their page | Wires |
| 3 | Update board | Table<br>Schematic | Footprints, in their region | Routes |
| 4 | Verify | Schematic<br>Board | ERC report<br>DRC report | — |
| 5 | Output | Schematic<br>Board | Netlist<br>Gerbers<br>Drill<br>Centroid<br>BOM | — |
| 6 | Source | Table | Price<br>Stock<br>Lifecycle | — |

**T3.2 — Re-entry**

A process adds what is missing and leaves what is there. A part already
placed keeps its position, its wiring and its routing. A process reports
what it found and did not touch; it deletes nothing.

## 4 — Tools

A tool is a document and a set of Python scripts. The agent follows the
document and calls the scripts. It writes code only to cover a gap in them,
and declares what it wrote so the gap can be closed.

Each tool document opens with the assets it reads and the assets it writes.

**T4.1 — Layout**

| Path | Holds |
|---|---|
| `board-build-tool.md` | This container |
| `define-parts.md` | One document per process |
| `update-sch.md` | |
| `update-pcb.md` | |
| `scripts/` | Shared, called by any tool |

**T4.2 — State**

| | |
|---|---|
| Written | This document |
| Built | Nothing. `builds/proto1/tools` is the working precedent, and has not been moved here |
