# Board-build

## Contents

1. [Introduction](#1--introduction)
2. [Data](#2--data)
3. [Assets](#3--assets)
4. [Processes](#4--processes)
5. [Tools](#5--tools)
6. [Installation](#6--installation)

## 1 — Introduction

### 1.1 — What it is

- Claude-assisted hardware design in KiCad.
- The tool carries:
  - The processes that carry a board through KiCad
  - The documents that define them
  - The scripts they call
- Nothing specific to one product.

### 1.2 — How it is used

- The board is designed by working processes 1–6 in order — as often as
  needed.
- Revision re-enters a process.
- The tool is entered at whichever process is next.

### 1.3 — How it works

- The LLM performs the process steps using ten sub-tools — T17.
- The sub-tool list is immutable:
  - No other tool is used or defined
  - No edit to the list is permitted
  - A document — or a version of one — that carries eleven is a bug

### 1.4 — The contract

- The structure — enforced and immutable:
  - The six processes — T14
  - The ten sub-tools — T17
  - The five tables with their keys — sections 2.3 and 2.4
  - The relations — T9
- The tool is stateless.
- Runtime exception: the User may add a field to any table at will.
- A tool changes the schema on the User's request and on nothing else:
  - Not to carry a task
  - Never on an inference
- An agent that finds it needs any of the structure changed has found a bug:
  - Abort
  - Declare the tool unusable
  - Name what it hit
  - Never carry on — never ask
- Failure of a tool or sub-tool is met the same way:
  - Abort
  - Notify
  - Bug — tool unusable

### 1.5 — How it is organized

- The tool lives in `tools/board-build/`.
- Work files live in the board directory.
- The project consists of — T13 is the full list:
  - `board.db`
  - The KiCad files
  - `lib/`
  - `datasheets/`

## 2 — Data

### 2.1 — The key

- The design keys to an **IPN** — an internal part number.
- An MPN hangs off an IPN; several MPNs may satisfy one.
- Parameters flow database to design: a field is edited in the database and
  pushed.

### 2.2 — Where the data lives

**T1 — Where the data lives**

| # | File | Holds |
|---|---|---|
| 1 | `board.db` | `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `offer_table` |
| 2 | `*.kicad_sch`, `*.kicad_pcb` | `Reference`, `Value`, `Footprint`, `ipn` |

- `board.db` is master and pushes to KiCad.
- Table names end in `_table`; keys carry the bare word.
- Design reads:
  - `parts_table`
  - `ref_table`
- Ordering reads:
  - `aml_table` — kept
  - `mpn_table` — kept
  - `offer_table` — fetched and discardable
- Sources:
  - The JLCPCB API
  - Distributor tables the User supplies
- The tables are a record of the design. Progress is a query — T2.

**T2 — Progress queries**

| # | Question | Query |
|---|---|---|
| 1 | Has a symbol | `symbol is null` |
| 2 | Has a footprint | `footprint is null` |
| 3 | Has a part number | A row in `aml_table` |
| 4 | Quantity | `count(*) from ref_table group by ipn` |
| 5 | The assembly | Rows whose `parent` is this IPN |

- `note` is a person's sentence — on `parts_table` and `aml_table`:
  - Normally blank
  - Tools write the columns

### 2.3 — The design tables

**T3 — `parts_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `ipn` | TEXT | Key | |
| 2 | `description` | TEXT | | Yes |
| 3 | `parent` | TEXT | | Yes |
| 4 | `symbol` | TEXT | | Yes |
| 5 | `footprint` | TEXT | | Yes |
| 6 | `source` | TEXT | | Yes |
| 7 | `note` | TEXT | | Yes |

**T4 — `ref_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `uuid` | TEXT | Key | |
| 2 | `ipn` | TEXT | | |
| 3 | `ref` | TEXT | | Yes |
| 4 | `page` | TEXT | | Yes |
| 5 | `room` | TEXT | | Yes |

- `parts_table` is the part — one row per IPN.
- `ref_table` is the instance: `U1` and `U2` are two rows on one `ipn`, each
  free to carry its own `page` and `room`.
- `uuid` is KiCad's instance UUID:
  - The `.kicad_sch` symbol carries it
  - The `.kicad_pcb` footprint holds `(path "/<sheet-uuid>/<symbol-uuid>")`
    back to it
- `ref` is a field. Reannotation in the editor is read back into it.
- `page` is the schematic page.
- `room` is the User's region: one name serving a block of the sheet and a
  region of the board.
- `parent` is an IPN — the part this one serves:
  - A feedback resistor carries the op-amp's IPN
  - Children are the assembly; the parent its primary part
  - Blank is top level
  - One level
- `source` is two letters — symbol then footprint — T5:
  - `h/-` is a hand-drawn symbol and no footprint
- `symbol` and `footprint` are `<project>:<name>` — resolving inside `lib/`
  per section 3:
  - `symbol-draw` and `footprint-draw` copy the object in and write the field
  - A KiCad library is an input to those tools; `source` records it as the
    origin

**T5 — `source` letters**

| # | Letter | Origin |
|---|---|---|
| 1 | `s` | KiCad stock libraries |
| 2 | `v` | Manufacturer or a publishing service |
| 3 | `h` | Drawn here, against the datasheet |
| 4 | `-` | Absent |

### 2.4 — The sourcing tables

**T6 — `aml_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `ipn` | TEXT | Key | |
| 2 | `mpn` | TEXT | Key | |
| 3 | `rank` | INTEGER | | Yes |
| 4 | `note` | TEXT | | Yes |

**T7 — `mpn_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `mpn` | TEXT | Key | |
| 2 | `manufacturer` | TEXT | | Yes |
| 3 | `datasheet` | TEXT | | Yes |

**T8 — `offer_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `mpn` | TEXT | Key | |
| 2 | `distributor` | TEXT | Key | |
| 3 | `break_qty` | INTEGER | Key | |
| 4 | `sku` | TEXT | | Yes |
| 5 | `currency` | TEXT | | Yes |
| 6 | `price` | REAL | | Yes |
| 7 | `stock` | INTEGER | | Yes |
| 8 | `moq` | INTEGER | | Yes |
| 9 | `lead_days` | INTEGER | | Yes |
| 10 | `fetched_at` | TEXT | | Yes |

- `aml_table` is the approved manufacturer list — the MPNs that may be built
  against an IPN:
  - The row is the approval
  - Every MPN in it takes the board as designed
  - `rank` orders the alternatives — blank on the one designed against
  - One blank per IPN — held as a unique index over `ipn` where
    `rank is null`
- `mpn_table` is the manufacturer part — maker and datasheet:
  - The row exists from the moment the part number is named
  - `datasheet-read` reads from the datasheet — lands in the footprint:
    - Package
    - Pin count
    - Pitch
- `offer_table` is fetched:
  - One row per distributor per quantity break
  - `fetched_at` dates it

### 2.5 — The relations

**T9 — The relations**

| # | From | To | Cardinality | On delete |
|---|---|---|---|---|
| 1 | `ref_table.ipn` | `parts_table.ipn` | Many-to-one | Restrict |
| 2 | `parts_table.parent` | `parts_table.ipn` | Many-to-one, self | Set null |
| 3 | `aml_table.ipn` | `parts_table.ipn` | Many-to-one | Restrict |
| 4 | `aml_table.mpn` | `mpn_table.mpn` | Many-to-one | Restrict |
| 5 | `offer_table.mpn` | `mpn_table.mpn` | Many-to-one | Cascade |

- All are declared foreign keys.
- Every tool sets `PRAGMA foreign_keys = ON`.

### 2.6 — The IPN

- `ANNNN` — class letter then four digits:
  - Sequential within the letter from `0001`
  - One number to one part for the life of the design
- A new IPN on change of:
  - Form
  - Fit
  - Function
- Any other change is a second MPN in `aml_table`.

**T10 — The IPN classes**

| # | Letter | Class |
|---|---|---|
| 1 | `A` | Amplifier |
| 2 | `C` | Capacitor |
| 3 | `E` | Antenna, panel |
| 4 | `F` | Filter |
| 5 | `G` | Synthesizer, PLL |
| 6 | `H` | Mechanical, enclosure |
| 7 | `J` | Connector |
| 8 | `K` | Switch |
| 9 | `L` | Inductor, ferrite |
| 10 | `M` | Mixer |
| 11 | `P` | Regulator, converter |
| 12 | `R` | Resistor |
| 13 | `S` | Sensor |
| 14 | `T` | Test point, cal standard |
| 15 | `U` | Processor, memory |
| 16 | `W` | Splitter, coupler, bias tee |
| 17 | `Y` | Oscillator, reference |

- Ten letters match the KiCad reference designator for the same class.
- Four digits mark the IPN: `U0001` is a part, `U1` an instance.
- T10 is the class list; a new class is added there first.

### 2.7 — What the schematic carries

**T11 — Schematic fields**

| # | Field | Origin |
|---|---|---|
| 1 | `Reference`, `Value`, `Footprint` | Built in |
| 2 | `ipn` | Custom, the key to `parts_table` |

- `Value` is drawn from the IPN.

### 2.8 — Where a thing is placed

**T12 — Placement**

| # | Rank | Dimension | Schematic | Board |
|---|---|---|---|---|
| 1 | 1 | `page` | Selects the file | — |
| 2 | 2 | `room` | Block of the sheet | Region of the board |
| 3 | 3 | Family | Groups instances whose `room` is blank | Groups instances whose `room` is blank |

- Placement follows this order.
- A family whose instances carry two pages is placed on both.
- Order of rooms and families within a page is arbitrary — subject to
  re-entry, section 4.4.
- Order within a family is defined in the tool documents.

## 3 — Assets

### 3.1 — The board project

**T13 — The board project**

| # | Asset | Owner |
|---|---|---|
| 1 | `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table` | Hand |
| 2 | `board.db` — `offer_table` | Fetched. Discardable |
| 3 | `lib/*.kicad_sym` | Hand |
| 4 | `lib/*.pretty` | Hand |
| 5 | `lib/3d/` | Hand |
| 6 | `datasheets/` | Hand |
| 7 | `*.kicad_pro` | Generated once |
| 8 | `*.kicad_sch` | Updated by the tool, wired by the User |
| 9 | `*.kicad_pcb` | Updated by the tool, routed by the User |
| 10 | Board setup — stackup, fabricator rules, DRC rules | Hand |
| 11 | `out/` — RF-simulation file | Generated |

### 3.2 — No dependencies — clone and work

- KiCad libraries are a source to copy from — never a reference.
- `sym-lib-table` and `fp-lib-table` sit in the project directory and are
  committed.
- Every path in them is `${KIPRJMOD}/lib/...`.
- Every model path in every `.kicad_mod` is `${KIPRJMOD}/lib/3d/...`.
- Nicknames are prefixed to the project, so a global entry on another
  machine cannot collide.
- `${KIPRJMOD}` is the only path variable, and every path is relative to it.
- Symbols, footprints and models are copied into `lib/`, and owned from that
  point.
- The check runs against a fresh clone, and fails on any of the above.

## 4 — Processes

### 4.1 — The chain

**T14 — The chain**

| # | Process | In | Out | Tools | User then |
|---|---|---|---|---|---|
| 1 | 0 — Init | The init state | `board.db`, five empty tables<br>`*.kicad_pro`<br>`sym-lib-table`<br>`lib/<project>.kicad_sym` | `db-init`<br>`kicad-init` | — |
| 2 | 1 — Update parts | Datasheet<br>Record row | `parts_table` row | `table-write` | — |
| 3 | 2 — Update library: symbol, footprint, 3D model | `board.db`<br>`datasheets/` | `lib/*.kicad_sym`<br>`lib/*.pretty`<br>`lib/3d/` | `datasheet-read`<br>`symbol-draw`<br>`footprint-draw` | — |
| 4 | 3 — Update schematic | `board.db`<br>`lib/*.kicad_sym` | `*.kicad_sch`<br>Symbols, on their page | `kicad-update` | Wires |
| 5 | 4 — Update board | `board.db`<br>`*.kicad_sch`<br>`lib/*.pretty`<br>`lib/3d/` | `*.kicad_pcb`<br>Footprints, placed | `kicad-update` | Routes |
| 6 | 5 — Output | `*.kicad_pcb` | RF-simulation file | — | — |
| 7 | 6 — Source | `aml_table` | Price<br>Stock<br>Availability | — | — |

- Process 1 gives a part its IPN and its description.
- Process 2 builds the library objects for the rows that lack them.

### 4.2 — The init state

- An empty board directory and a pointer to the raw source the parts come
  from. Nothing else:
  - No `board.db`
  - No `lib/`
  - No KiCad files
- The board directory carries no product name and no reference of its own.
- The source is named to the process that reads it and lives outside the
  board directory.
- Every file in the board directory is made by a tool — a full init deletes
  all of them and starts from nothing.

### 4.3 — Process 0 — Init

From the init state, three steps, in this order — T15.

**T15 — Init steps**

| # | Step | Tool | Makes |
|---|---|---|---|
| 1 | 1 | `db-init` | `board.db` and its five empty tables |
| 2 | 2 | `table-write` | `parts_table`, `ref_table` and `aml_table`, from the reference |
| 3 | 3 | `kicad-init` | `*.kicad_pro`, `sym-lib-table`, `lib/<project>.kicad_sym` |

- Steps 1 and 3 are process 0; step 2 is process 1.
- After them the board directory holds the record and an empty project —
  process 2 has somewhere to put a symbol.

### 4.4 — Re-entry

- A process adds what is missing and leaves what is there.
- A placed part keeps:
  - Its position
  - Its wiring
  - Its routing
- Each run reports what it left untouched.

### 4.5 — The RF-simulation file

- Process 5 writes what `rf-simulation` reads.
- Three-dimensional geometry is carried on the KiCad User layers:
  - Each layer names a vertical position and a height
  - The objects on it are the boxes at that level — dielectric or conductor

### 4.6 — One agent per process

- Each process is entered on its own and calls the tools its T14 row names.
- They are written in order — 1 and 2, then 3 through 6 — each agreed
  working before the next.
- A process that needs the User mid-run is a command — loaded into the
  running session.
- A process that runs headless is an agent — holding its own context and
  reporting at the end.

**T16 — Process forms**

| # | Process | Form |
|---|---|---|
| 1 | 1 — Update parts | Command |
| 2 | 2 — Update library | Agent. Asks when a datasheet withholds the pinout |
| 3 | 3 — Update schematic | Agent |
| 4 | 4 — Update board | Agent |
| 5 | 5 — Output | Agent |
| 6 | 6 — Source | Agent |

## 5 — Tools

### 5.1 — What a tool is

- A tool is LLM-based: defined by one document and zero or one Python
  script.
- The LLM follows the document and may call the script:
  - Script — deterministic file and database work
  - LLM — interpretation and judgment
- Code the agent writes to cover a gap is declared — so the gap can be
  closed.
- A process uses one or more tools; a tool serves one or more processes.
- Each tool document opens with the assets it reads and writes.

### 5.2 — The tools

There are ten.

**T17 — The tools**

| # | Tool | Function | In | Out |
|---|---|---|---|---|
| 1 | `db-init` | Create the database and its tables | T3, T4, T6, T7, T8 | `board.db` |
| 2 | `table-read` | Show the record, one view per workflow step | `board.db` | Markdown on stdout |
| 3 | `lib-index` | Index the KiCad symbol libraries | The User's `.kicad_sym` files | `lib/kicad-index.json` |
| 4 | `copy-kicad-part` | Find a symbol for a part in the KiCad libraries | `board.db`, KiCad libraries | `<library>:<symbol>`, or `null` |
| 5 | `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| 6 | `symbol-draw` | Copy or draw a symbol into `lib/` | KiCad libraries, pins from `datasheet-read` | `lib/*.kicad_sym`<br>`parts_table` — `symbol`, `source` |
| 7 | `footprint-draw` | Copy or draw a footprint into `lib/` | KiCad libraries, package from `datasheet-read` | `lib/*.pretty`, `lib/3d/`<br>`parts_table` — `footprint`, `source` |
| 8 | `table-write` | Create or modify part | Record row | `board.db` — `parts_table`, `ref_table`, `aml_table` |
| 9 | `kicad-init` | Create the KiCad project from nothing | `board.db` | `*.kicad_pro`, `*.kicad_sch`, `sym-lib-table`, `lib/` |
| 10 | `kicad-update` | Push the record into the KiCad project | `board.db`, `lib/` | `*.kicad_sch`, `*.kicad_pcb` |

### 5.3 — Layout

- `board-build-tool.md` — the only document at the top level
- `tools/` — one `<tool>.md` and its scripts, per tool
- `.claude-plugin/` — `plugin.json`, `marketplace.json`
- `commands/` — one per interactive process, section 4.6
- `agents/` — one per batch process, section 4.6
- `skills/` — skills they load

## 6 — Installation

- Claude Code reads its assets from fixed paths — the folder is carried as a
  plugin and its files stay tool assets — T18.

**T18 — Plugin paths**

| # | Path | Holds |
|---|---|---|
| 1 | `.claude-plugin/plugin.json` | The plugin manifest |
| 2 | `.claude-plugin/marketplace.json` | The local marketplace entry |
| 3 | `commands/<process>.md` | One command per interactive process |
| 4 | `agents/<process>.md` | One agent per batch process |
| 5 | `skills/<name>/SKILL.md` | Skills the commands and agents load |

- A fresh clone installs it once:

```
/plugin marketplace add ./tools/board-build
/plugin install board-build
```
