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
| `design.db` | `parts_table`, `ref_table` | Master for the design fields. Pushes to KiCad |
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
| `parts_table` | `ipn` | `description`, `category`, `symbol`, `footprint`, `model`, `pins_checked`, `source`, `status` |
| `ref_table` | `ref` | `ipn`, `page`, `room` |

`parts_table` is the part. One row per IPN, whatever the board uses it for.

`ref_table` is the instance. `U1` and `U2` are two rows carrying one `ipn`,
and they are free to sit on different pages and in different rooms. A part
used forty times is one `parts_table` row and forty `ref_table` rows.

There is no `qty` field. Quantity is `count(*) from ref_table group by ipn`,
and a field would only be a second place for it to be wrong.

`page` names the schematic page the symbol is placed on. `room` is a tag the
tool may use in choosing where a footprint goes; it constrains nothing, and a
KiCad group is nothing more than a list of parts. Both are properties of the
instance, not of the part.

`source` records where the library object came from — `stock`, `vendor` or
`hand`. Once a symbol is copied into `lib/` under the clone rule of section
2, nothing else distinguishes a copied stock symbol from a drawn one.

**Which way `ref` flows**

Every other field is pushed from the database to KiCad. `ref` is not.
Annotation happens in the editor, so `ref_table` is written from the sheets
after annotation, and is the one place the rule of this section is reversed.

**The IPN**

`ANNNN`. One letter for the part class, from the list below, then four
digits. The digits are sequential inside that letter, from `0001`, and are
never reused.

No project prefix — the database is the project. No revision suffix — a
change of form, fit or function is a new IPN, and a change that is none of
those is a second MPN against the same one, which is what `aml_table` holds.

| Letter | Class |
|---|---|
| `A` | amplifier |
| `C` | capacitor |
| `E` | antenna, panel |
| `F` | filter |
| `G` | synthesizer, PLL |
| `H` | mechanical, enclosure |
| `J` | connector |
| `K` | switch |
| `L` | inductor, ferrite |
| `M` | mixer |
| `P` | regulator, converter |
| `R` | resistor |
| `S` | sensor |
| `T` | test point, cal standard |
| `U` | processor, memory |
| `W` | splitter, coupler, bias tee |
| `Y` | oscillator, reference |

Ten of them are the KiCad reference-designator letter for the same thing, so
the letter reads as itself to anyone who has opened a schematic.

An IPN therefore looks like a reference designator — `U0001` beside `U1`.
The four fixed digits are what keep them apart, and the IPN never reaches the
`Value` field in any case.

`category` carries the class word, so the letter is not the only place the
class is recorded. A class the list does not hold is added here first.

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

A git clone has to work out of the box. Into an empty directory, on a fresh
KiCad install, the project opens and edits with nothing missing. No library,
footprint or model resolves outside the repository.

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

| # | Process | In | Out | Tools | User then |
|---|---|---|---|---|---|
| 1 | Update parts | Datasheet<br>Record row | `parts_table` row | `table-write` | — |
| 2 | Update library | `design.db`<br>`datasheets/` | `lib/*.kicad_sym`<br>`lib/*.pretty`<br>`lib/3d/` | `datasheet-read`<br>`symbol-draw`<br>`footprint-draw` | — |
| 3 | Update schematic | `design.db`<br>`lib/*.kicad_sym` | `*.kicad_sch`<br>Symbols, on their page | `sheet-place` | Wires |
| 4 | Update board | `design.db`<br>`*.kicad_sch`<br>`lib/*.pretty` | `*.kicad_pcb`<br>Footprints, placed | `board-place` | Routes |
| 5 | Output | `*.kicad_pcb` | RF-simulation file | `layer-export` | — |
| 6 | Source | `aml_table` | Price<br>Stock<br>Availability | `stock-query` | — |

Defining a part and building its library objects are separate. A part is
picked, described and given an IPN in process 1; the symbol, footprint and
model are built in process 2, from the rows that are missing them. One is
hand work against a schema, the other is a batch run.

`db-init` and `clone-check` sit outside the chain. `db-init` runs once, on
an empty board project, before process 1. `clone-check` runs after process 2.

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

Each process in T3.1 is entered on its own and calls the tools its row names.
They are written one at a time, each agreed working before the next is
started: 1 and 2, then 3, then 4, then 5, then 6.

A process that needs the User mid-run is a command, loaded into the running
session. A process that runs to completion on its own is an agent, holding
its own context and reporting at the end. A subagent cannot ask a question,
so the split is not a preference.

| # | Process | Form |
|---|---|---|
| 1 | Update parts | Command |
| 2 | Update library | Agent. Asks only when a datasheet does not carry the pinout |
| 3 | Update schematic | Agent |
| 4 | Update board | Agent |
| 5 | Output | Agent |
| 6 | Source | Agent |

The files are tool assets and live in this folder, not in `.claude/`. Claude
Code reads commands, agents and skills only from fixed paths, so the folder
is carried as a plugin:

| Path | Holds |
|---|---|
| `.claude-plugin/plugin.json` | the plugin manifest |
| `.claude-plugin/marketplace.json` | the local marketplace entry |
| `commands/<process>.md` | one command per interactive process |
| `agents/<process>.md` | one agent per batch process |
| `skills/<name>/SKILL.md` | skills the commands and agents load |

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
| `db-init` | Create the databases and their tables | T1.2, T1.3 | `design.db`, `sourcing.db` |
| `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| `symbol-draw` | Create or modify symbol | Pins from `datasheet-read` | `lib/*.kicad_sym` |
| `footprint-draw` | Create or modify footprint | Package from `datasheet-read` | `lib/*.pretty`, `lib/3d/` |
| `table-write` | Create or modify part | Record row, `datasheet-read` | `design.db` — `parts_table` |
| `sheet-place` | Place symbols on their page | `design.db`, `lib/*.kicad_sym` | `*.kicad_sch` |
| `board-place` | Place footprints on the board | `design.db`, `*.kicad_sch`, `lib/*.pretty` | `*.kicad_pcb` |
| `layer-export` | Export the board geometry as boxes | `*.kicad_pcb` | `out/` — the RF-simulation file |
| `stock-query` | Fetch price, stock and lifecycle | `sourcing.db` — `aml_table`, JLCPCB API | `sourcing.db` — `mpn_table`, `offer_table` |
| `clone-check` | Prove a fresh clone opens with nothing missing | A fresh clone of the project | A verdict |

**Layout**

- `board-build-tool.md` — the only document at the top level
- `tools/` — one `<tool>.md` and its scripts, per tool
- `.claude-plugin/` — `plugin.json`, `marketplace.json`
- `commands/` — one per interactive process, T3.2
- `agents/` — one per batch process, T3.2
- `skills/` — skills they load

**State**

- Written: this document, `tools/db-init.md`.
- Built: `tools/db-init.py`.
- `builds/proto1/tools` is the working precedent for the rest, and has not
  been moved here.
