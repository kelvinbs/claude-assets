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

**Scope**

- Covers the KiCad design process, from the parts data to the outputs.
- Entered at any process, as often as the design needs.

**The split**

- Tool
  - The parts data, on the User's decisions
  - Symbols, footprints, models
  - Placing symbols on their page
  - Footprint assignment
  - Placing footprints on the board
  - The RF-simulation file
- User
  - Wiring
  - Board outline and stackup
  - Routing

**The data**

The design keys to an **IPN** — an internal part number, this project's own
handle for a part. It is not a manufacturer part number. An MPN is data
hanging off an IPN, and several MPNs may satisfy one.

Parameters flow one way, database to design. A field is never edited on the
sheet.

**T1.1 — Where the data lives**

| File | Tables | Role |
|---|---|---|
| `design.db` | `parts` | Master for design fields. Pushes to KiCad |
| `*.kicad_sch`, `*.kicad_pcb` | — | The native design store: `Reference`, `Value`, `Footprint`, `ipn` |
| `sourcing.db` | `aml`, `mpns`, `offers` | Order time. `mpns` and `offers` are fetched and refetchable |

Every file is tracked and pushed. Nothing here is untracked.

KiCad is the native store for design use, generally updated from the master.
Ordering reads `sourcing.db` and never writes a design file. Design never
reads `sourcing.db`. The join between them is `aml.ipn`.

Availability and price come from the JLCPCB API, the only distributor
interface that answers, or from distributor tables the User downloads and
hands over.

**T1.2 — The tables**

Tables are plural, keys singular. No name does double duty.

| File | Table | Key | Fields |
|---|---|---|---|
| `design.db` | `parts` | `ipn` | `description`, `category`, `symbol`, `footprint`, `model`, `pins_checked`, `page`, `room`, `qty`, `status` |
| `sourcing.db` | `aml` | `ipn` + `mpn` | `rank`, `approved`, `approved_by`, `approved_on`, `drop_in`, `note` |
| `sourcing.db` | `mpns` | `mpn` | `manufacturer`, `package`, `pin_count`, `pitch_mm`, `datasheet`, `lifecycle`, `fetched_at` |
| `sourcing.db` | `offers` | `mpn` + `distributor` + `break_qty` | `sku`, `currency`, `price`, `stock`, `moq`, `lead_days`, `fetched_at` |

`page` and `room` are fields, not tables. `page` names the schematic page the
symbol is placed on. `room` is a tag the tool may use in choosing where a
footprint goes; it constrains nothing, and a KiCad group is nothing more than
a list of parts.

`aml` is the approved manufacturer list: which MPNs may be built against an
IPN, ranked, each approval dated and attributed.

**What the schematic carries**

- `Reference`, `Value` and `Footprint`, all built in
- `ipn`, the key back to `design.db`, which is not

`Value` is drawn from the IPN, not from a manufacturer part number — which of
`ipn.description` or the ranked `aml` MPN fills it is not yet settled.

## 2 — Assets

**T2.1 — The board project**

| Asset | Owner |
|---|---|
| `design.db` | Hand |
| `sourcing.db` — `aml` | Hand |
| `sourcing.db` — `mpn`, `offer` | Fetched. Discardable |
| `lib/*.kicad_sym` | Hand |
| `lib/*.pretty` | Hand |
| `lib/3d/` | Hand |
| `datasheets/` | Hand |
| `*.kicad_pro` | Generated once |
| `*.kicad_sch` | Updated by the tool, wired by the User |
| `*.kicad_pcb` | Updated by the tool, routed by the User |
| Board setup — stackup, fabricator rules, DRC rules | Hand |
| `out/` — RF-simulation file | Generated |

**The clone**

The file organisation exists for one reason. A clone into an empty
directory, on a fresh KiCad install, must open and edit with nothing
missing. No library, footprint or model resolves outside the repository.

- `sym-lib-table` and `fp-lib-table` sit in the project directory and are committed.
- Every path in them is `${KIPRJMOD}/lib/...`.
- Every model path in every `.kicad_mod` is `${KIPRJMOD}/lib/3d/...`.
- Nicknames are prefixed to the project, so a global entry on another
  machine cannot collide.
- No other path variable appears, and no absolute path.
- Symbols, footprints and models are copied into `lib/`, and owned from that
  point.

The check runs against a fresh clone rather than the working copy, and
fails on any of the above.

## 3 — Processes

**T3.1 — The chain**

A process takes the output of the one before it. Each uses one or more of
the tools in section 4.

| # | Process | In | Out | User then |
|---|---|---|---|---|
| 1 | Define parts | Datasheet<br>Record row | `parts` row<br>Symbol<br>Footprint<br>Model | — |
| 2 | Update schematic | `design.db` | `*.kicad_sch`<br>Symbols, on their page | Wires |
| 3 | Update board | `design.db`<br>`*.kicad_sch` | `*.kicad_pcb`<br>Footprints, placed | Routes |
| 4 | Output | `*.kicad_pcb` | RF-simulation file | — |
| 5 | Source | `aml` | Price<br>Stock<br>Availability | — |

**The RF-simulation file**

Output writes the files `rf-simulation` reads. Geometry in three dimensions
is carried on the KiCad User layers. Each layer names a vertical position
and a height; the objects drawn on it are the boxes at that level,
dielectric or conductor. The naming grammar and the export are defined
later.

**Re-entry**

A process adds what is missing and leaves what is there. A part already
placed keeps its position, its wiring and its routing. A process reports
what it found and did not touch; it deletes nothing.

## 4 — Tools

A tool is a document and a set of Python scripts. The agent follows the
document and calls the scripts. It writes code only to cover a gap in them,
and declares what it wrote so the gap can be closed.

A process uses one or more tools. A tool serves one or more processes. The
two carry different names.

Each tool document opens with the assets it reads and the assets it writes.

**T4.1 — The tools**

| Tool | Used by |
|---|---|
| `datasheet-read` | Define parts |
| `symbol-draw` | Define parts |
| `footprint-draw` | Define parts |
| `table-write` | Define parts |
| `sheet-place` | Update schematic |
| `board-place` | Update board |
| `layer-export` | Output |
| `stock-query` | Source |
| `clone-check` | Any |

**Layout**

- `board-build-tool.md` — this container
- `<tool>.md` — one document per tool
- `scripts/` — shared, called by any tool

**State**

- Written: this document.
- Built: nothing. `builds/proto1/tools` is the working precedent, and has
  not been moved here.
