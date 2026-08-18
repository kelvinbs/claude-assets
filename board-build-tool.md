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

Two databases and the KiCad files. Every table name ends in `_table`; a key
never carries the suffix, so a table and its key are never the same word.

| File | Holds | Role |
|---|---|---|
| `design.db` | `parts_table` | Master for the design fields. Pushes to KiCad |
| `*.kicad_sch`, `*.kicad_pcb` | — | The native design store: `Reference`, `Value`, `Footprint`, `ipn` |
| `sourcing.db` | `aml_table`, `mpn_table`, `offer_table` | Order time |

KiCad is the native store for design use, generally updated from the master.
Design never reads `sourcing.db`; ordering never writes a design file. The
join between them is `ipn`.

`mpn_table` and `offer_table` are fetched, never typed, and may be discarded
and fetched again. `aml_table` is an approval and is kept.

Availability and price come from the JLCPCB API, the only distributor
interface that answers, or from distributor tables the User downloads and
hands over.

**T1.2 — `design.db`**

| Table | Key | Fields |
|---|---|---|
| `parts_table` | `ipn` | `description`, `category`, `symbol`, `footprint`, `model`, `pins_checked`, `page`, `room`, `qty`, `status` |

`page` names the schematic page the symbol is placed on. `room` is a tag the
tool may use in choosing where a footprint goes; it constrains nothing, and a
KiCad group is nothing more than a list of parts.

**T1.3 — `sourcing.db`**

| Table | Key | Fields |
|---|---|---|
| `aml_table` | `ipn` + `mpn` | `rank`, `approved`, `approved_by`, `approved_on`, `drop_in`, `note` |
| `mpn_table` | `mpn` | `manufacturer`, `package`, `pin_count`, `pitch_mm`, `datasheet`, `lifecycle`, `fetched_at` |
| `offer_table` | `mpn` + `distributor` + `break_qty` | `sku`, `currency`, `price`, `stock`, `moq`, `lead_days`, `fetched_at` |

`aml_table` is the approved manufacturer list: which MPNs may be built against
an IPN, ranked, each approval dated and attributed.

An offer is one distributor's listing of one MPN at one quantity break. One
MPN carries many.

**What the schematic carries**

- `Reference`, `Value` and `Footprint`, all built in
- `ipn`, the key back to `design.db`, which is not

`Value` is drawn from the IPN, not from a manufacturer part number — which of
`ipn.description` or the ranked `aml_table` MPN fills it is not yet settled.

## 2 — Assets

**T2.1 — The board project**

| Asset | Owner |
|---|---|
| `design.db` | Hand |
| `sourcing.db` — `aml_table` | Hand |
| `sourcing.db` — `mpn_table`, `offer_table` | Fetched. Discardable |
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
| 1 | Define or modify parts | Datasheet<br>Record row | `parts_table` row<br>Symbol<br>Footprint<br>Model | — |
| 2 | Update schematic | `design.db` | `*.kicad_sch`<br>Symbols, on their page | Wires |
| 3 | Update board | `design.db`<br>`*.kicad_sch` | `*.kicad_pcb`<br>Footprints, placed | Routes |
| 4 | Output | `*.kicad_pcb` | RF-simulation file | — |
| 5 | Source | `aml_table` | Price<br>Stock<br>Availability | — |

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

**T3.2 — One agent per process**

Each process in T3.1 is an agent. The agent is entered on its own, holds its
own context, and calls the tools T3.1 gives it. Agents are written one at a
time and each is agreed working before the next is started; the order is
processes 1 and 2 together, then 3, then 4, then 5.

The agent files are tool assets and live in this folder, not in `.claude/`.
Claude Code reads agents and skills only from fixed paths, so the folder is
carried as a plugin:

| Path | Holds |
|---|---|
| `.claude-plugin/plugin.json` | the plugin manifest |
| `.claude-plugin/marketplace.json` | the local marketplace entry |
| `agents/<process>.md` | one agent per process |
| `skills/<name>/SKILL.md` | skills the agents load |

A fresh clone installs it once:

```
/plugin marketplace add ./tools/board-build
/plugin install board-build
```

## 4 — Tools

A tool is a document and a set of Python scripts. The agent follows the
document and calls the scripts. It writes code only to cover a gap in them,
and declares what it wrote so the gap can be closed.

A process uses one or more tools. A tool serves one or more processes. The
two carry different names.

Each tool document opens with the assets it reads and the assets it writes.

**T4.1 — The tools**

| Tool | Function | In | Out |
|---|---|---|---|
| `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| `symbol-draw` | Create or modify symbol | Pins from `datasheet-read` | `lib/*.kicad_sym` |
| `footprint-draw` | Create or modify footprint | Package from `datasheet-read` | `lib/*.pretty`, `lib/3d/` |
| `table-write` | Create or modify part | Record row, `datasheet-read` | `design.db` — `parts_table` |
| `sheet-place` | Place symbols on their page | `design.db`, `lib/*.kicad_sym` | `*.kicad_sch` |
| `board-place` | Place footprints on the board | `design.db`, `*.kicad_sch`, `lib/*.pretty` | `*.kicad_pcb` |
| `layer-export` | Export the board geometry as boxes | `*.kicad_pcb` | `out/` — the RF-simulation file |
| `stock-query` | Fetch price, stock and lifecycle | `sourcing.db` — `aml_table`, JLCPCB API | `sourcing.db` — `mpn_table`, `offer_table` |
| `clone-check` | Prove a fresh clone opens with nothing missing | A fresh clone of the project | A verdict |

Which process uses which tool is T3.1.

**Layout**

- `board-build-tool.md` — this container
- `<tool>.md` — one document per tool
- `scripts/` — shared, called by any tool
- `.claude-plugin/` — `plugin.json`, `marketplace.json`
- `agents/` — one agent per process, T3.2
- `skills/` — skills the agents load

**State**

- Written: this document.
- Built: nothing. `builds/proto1/tools` is the working precedent, and has
  not been moved here.
