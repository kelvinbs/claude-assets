# Board-build

## Contents

1. [Introduction](#1--introduction)
2. [Data](#2--data)
3. [Assets](#3--assets)
4. [Processes](#4--processes)
5. [Tools](#5--tools)

## 1 — Introduction

### 1.1 — What it is

- Claude-assisted hardware design in KiCad.
- The tool carries:
  - the processes that carry a board through KiCad
  - the documents that define them
  - the scripts they call
- Nothing specific to one product.

### 1.2 — How it is used

- The board is designed by working processes 1–6 in order — as often as
  needed.
- Revision re-enters a process.
- The tool is entered at whichever process is next.

### 1.3 — How it works

- The LLM performs the process steps using ten sub-tools — T19.
- The sub-tool list is immutable:
  - no other tool is used or defined
  - no edit to the list is permitted
  - a document — or a version of one — that carries eleven is a bug

### 1.4 — The contract

- The structure — enforced and immutable:
  - the six processes — T15
  - the ten sub-tools — T19
  - the five tables with their keys — sections 2.3 and 2.4
  - the relations — T10
- The tool is stateless.
- Runtime exception: the User may add a field to any table at will.
- A tool changes the schema on the User's request and on nothing else:
  - not to carry a task
  - never on an inference
- An agent that finds it needs any of the structure changed has found a bug:
  - abort
  - declare the tool unusable
  - name what it hit
  - never carry on — never ask
- Failure of a tool or sub-tool is met the same way:
  - abort
  - notify
  - bug — tool unusable

### 1.5 — The split

**T1 — The split**

| # | Tool | User |
|---|---|---|
| r1 | the parts data, on the User's decisions | wiring |
| r2 | symbols, footprints, models | board outline and stackup |
| r3 | symbol placement, footprint assignment and placement | routing |
| r4 | the RF-simulation file | |

### 1.6 — How it is organized

- The tool lives in `tools/board-build/`.
- Work files live in the board directory.
- The project consists of — T14 is the full list:
  - `board.db`
  - the KiCad files
  - `lib/`
  - `datasheets/`

## 2 — Data

### 2.1 — The key

- The design keys to an **IPN** — an internal part number.
- An MPN hangs off an IPN; several MPNs may satisfy one.
- Parameters flow database to design: a field is edited in the database and
  pushed.

### 2.2 — Where the data lives

**T2 — Where the data lives**

| # | File | Holds |
|---|---|---|
| r1 | `board.db` | `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `offer_table` |
| r2 | `*.kicad_sch`, `*.kicad_pcb` | `Reference`, `Value`, `Footprint`, `ipn` |

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
  - the JLCPCB API
  - distributor tables the User supplies
- The tables are a record of the design. Progress is a query — T3.

**T3 — Progress queries**

| # | Question | Query |
|---|---|---|
| r1 | has a symbol | `symbol is null` |
| r2 | has a footprint | `footprint is null` |
| r3 | has a part number | a row in `aml_table` |
| r4 | quantity | `count(*) from ref_table group by ipn` |
| r5 | the assembly | rows whose `parent` is this IPN |

- `note` is a person's sentence — on `parts_table` and `aml_table`:
  - normally blank
  - tools write the columns

### 2.3 — The design tables

**T4 — `parts_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| r1 | `ipn` | TEXT | key | |
| r2 | `description` | TEXT | | yes |
| r3 | `parent` | TEXT | | yes |
| r4 | `symbol` | TEXT | | yes |
| r5 | `footprint` | TEXT | | yes |
| r6 | `source` | TEXT | | yes |
| r7 | `note` | TEXT | | yes |

**T5 — `ref_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| r1 | `uuid` | TEXT | key | |
| r2 | `ipn` | TEXT | | |
| r3 | `ref` | TEXT | | yes |
| r4 | `page` | TEXT | | yes |
| r5 | `room` | TEXT | | yes |

- `parts_table` is the part — one row per IPN.
- `ref_table` is the instance: `U1` and `U2` are two rows on one `ipn`, each
  free to carry its own `page` and `room`.
- `uuid` is KiCad's instance UUID:
  - the `.kicad_sch` symbol carries it
  - the `.kicad_pcb` footprint holds `(path "/<sheet-uuid>/<symbol-uuid>")`
    back to it
- `ref` is a field. Reannotation in the editor is read back into it.
- `page` is the schematic page.
- `room` is the User's region: one name serving a block of the sheet and a
  region of the board.
- `parent` is an IPN — the part this one serves:
  - a feedback resistor carries the op-amp's IPN
  - children are the assembly; the parent its primary part
  - blank is top level
  - one level
- `source` is two letters — symbol then footprint — T6:
  - `h/-` is a hand-drawn symbol and no footprint
- `symbol` and `footprint` are `<project>:<name>` — resolving inside `lib/`
  per section 3:
  - `symbol-draw` and `footprint-draw` copy the object in and write the field
  - a KiCad library is an input to those tools; `source` records it as the
    origin

**T6 — `source` letters**

| # | Letter | Origin |
|---|---|---|
| r1 | `s` | KiCad stock libraries |
| r2 | `v` | manufacturer or a publishing service |
| r3 | `h` | drawn here, against the datasheet |
| r4 | `-` | absent |

### 2.4 — The sourcing tables

**T7 — `aml_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| r1 | `ipn` | TEXT | key | |
| r2 | `mpn` | TEXT | key | |
| r3 | `rank` | INTEGER | | yes |
| r4 | `note` | TEXT | | yes |

**T8 — `mpn_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| r1 | `mpn` | TEXT | key | |
| r2 | `manufacturer` | TEXT | | yes |
| r3 | `datasheet` | TEXT | | yes |

**T9 — `offer_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| r1 | `mpn` | TEXT | key | |
| r2 | `distributor` | TEXT | key | |
| r3 | `break_qty` | INTEGER | key | |
| r4 | `sku` | TEXT | | yes |
| r5 | `currency` | TEXT | | yes |
| r6 | `price` | REAL | | yes |
| r7 | `stock` | INTEGER | | yes |
| r8 | `moq` | INTEGER | | yes |
| r9 | `lead_days` | INTEGER | | yes |
| r10 | `fetched_at` | TEXT | | yes |

- `aml_table` is the approved manufacturer list — the MPNs that may be built
  against an IPN:
  - the row is the approval
  - every MPN in it takes the board as designed
  - `rank` orders the alternatives — blank on the one designed against
  - one blank per IPN — held as a unique index over `ipn` where
    `rank is null`
- `mpn_table` is the manufacturer part — maker and datasheet:
  - the row exists from the moment the part number is named
  - `datasheet-read` reads from the datasheet — lands in the footprint:
    - package
    - pin count
    - pitch
- `offer_table` is fetched:
  - one row per distributor per quantity break
  - `fetched_at` dates it

### 2.5 — The relations

**T10 — The relations**

| # | From | To | Cardinality | On delete |
|---|---|---|---|---|
| r1 | `ref_table.ipn` | `parts_table.ipn` | many-to-one | restrict |
| r2 | `parts_table.parent` | `parts_table.ipn` | many-to-one, self | set null |
| r3 | `aml_table.ipn` | `parts_table.ipn` | many-to-one | restrict |
| r4 | `aml_table.mpn` | `mpn_table.mpn` | many-to-one | restrict |
| r5 | `offer_table.mpn` | `mpn_table.mpn` | many-to-one | cascade |

- All are declared foreign keys.
- Every tool sets `PRAGMA foreign_keys = ON`.

### 2.6 — The IPN

- `ANNNN` — class letter then four digits:
  - sequential within the letter from `0001`
  - one number to one part for the life of the design
- A new IPN on change of:
  - form
  - fit
  - function
- Any other change is a second MPN in `aml_table`.

**T11 — The IPN classes**

| # | Letter | Class |
|---|---|---|
| r1 | `A` | amplifier |
| r2 | `C` | capacitor |
| r3 | `E` | antenna, panel |
| r4 | `F` | filter |
| r5 | `G` | synthesizer, PLL |
| r6 | `H` | mechanical, enclosure |
| r7 | `J` | connector |
| r8 | `K` | switch |
| r9 | `L` | inductor, ferrite |
| r10 | `M` | mixer |
| r11 | `P` | regulator, converter |
| r12 | `R` | resistor |
| r13 | `S` | sensor |
| r14 | `T` | test point, cal standard |
| r15 | `U` | processor, memory |
| r16 | `W` | splitter, coupler, bias tee |
| r17 | `Y` | oscillator, reference |

- Ten letters match the KiCad reference designator for the same class.
- Four digits mark the IPN: `U0001` is a part, `U1` an instance.
- T11 is the class list; a new class is added there first.

### 2.7 — What the schematic carries

**T12 — Schematic fields**

| # | Field | Origin |
|---|---|---|
| r1 | `Reference`, `Value`, `Footprint` | built in |
| r2 | `ipn` | custom, the key to `parts_table` |

- `Value` is drawn from the IPN.

### 2.8 — Where a thing is placed

**T13 — Placement**

| # | Rank | Dimension | Schematic | Board |
|---|---|---|---|---|
| r1 | 1 | `page` | selects the file | — |
| r2 | 2 | `room` | block of the sheet | region of the board |
| r3 | 3 | family | groups instances whose `room` is blank | groups instances whose `room` is blank |

- Placement follows this order.
- A family whose instances carry two pages is placed on both.
- Order of rooms and families within a page is arbitrary — subject to
  re-entry, section 4.4.
- Order within a family is defined in the tool documents.

## 3 — Assets

### 3.1 — The board project

**T14 — The board project**

| # | Asset | Owner |
|---|---|---|
| r1 | `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table` | Hand |
| r2 | `board.db` — `offer_table` | Fetched. Discardable |
| r3 | `lib/*.kicad_sym` | Hand |
| r4 | `lib/*.pretty` | Hand |
| r5 | `lib/3d/` | Hand |
| r6 | `datasheets/` | Hand |
| r7 | `*.kicad_pro` | Generated once |
| r8 | `*.kicad_sch` | Updated by the tool, wired by the User |
| r9 | `*.kicad_pcb` | Updated by the tool, routed by the User |
| r10 | Board setup — stackup, fabricator rules, DRC rules | Hand |
| r11 | `out/` — RF-simulation file | Generated |

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

**T15 — The chain**

| # | Process | In | Out | Tools | User then |
|---|---|---|---|---|---|
| r1 | 0 — Init | The init state | `board.db`, five empty tables<br>`*.kicad_pro`<br>`sym-lib-table`<br>`lib/<project>.kicad_sym` | `db-init`<br>`kicad-init` | — |
| r2 | 1 — Update parts | Datasheet<br>Record row | `parts_table` row | `table-write` | — |
| r3 | 2 — Update library: symbol, footprint, 3D model | `board.db`<br>`datasheets/` | `lib/*.kicad_sym`<br>`lib/*.pretty`<br>`lib/3d/` | `datasheet-read`<br>`symbol-draw`<br>`footprint-draw` | — |
| r4 | 3 — Update schematic | `board.db`<br>`lib/*.kicad_sym` | `*.kicad_sch`<br>Symbols, on their page | `kicad-update` | Wires |
| r5 | 4 — Update board | `board.db`<br>`*.kicad_sch`<br>`lib/*.pretty`<br>`lib/3d/` | `*.kicad_pcb`<br>Footprints, placed | `kicad-update` | Routes |
| r6 | 5 — Output | `*.kicad_pcb` | RF-simulation file | — | — |
| r7 | 6 — Source | `aml_table` | Price<br>Stock<br>Availability | — | — |

- Process 1 gives a part its IPN and its description.
- Process 2 builds the library objects for the rows that lack them.

### 4.2 — The init state

- An empty board directory and a pointer to the raw source the parts come
  from. Nothing else:
  - no `board.db`
  - no `lib/`
  - no KiCad files
- The board directory carries no product name and no reference of its own.
- The source is named to the process that reads it and lives outside the
  board directory.
- Every file in the board directory is made by a tool — a full init deletes
  all of them and starts from nothing.

### 4.3 — Process 0 — Init

From the init state, three steps, in this order — T16.

**T16 — Init steps**

| # | Step | Tool | Makes |
|---|---|---|---|
| r1 | 1 | `db-init` | `board.db` and its five empty tables |
| r2 | 2 | `table-write` | `parts_table`, `ref_table` and `aml_table`, from the reference |
| r3 | 3 | `kicad-init` | `*.kicad_pro`, `sym-lib-table`, `lib/<project>.kicad_sym` |

- Steps 1 and 3 are process 0; step 2 is process 1.
- After them the board directory holds the record and an empty project —
  process 2 has somewhere to put a symbol.

### 4.4 — Re-entry

- A process adds what is missing and leaves what is there.
- A placed part keeps:
  - its position
  - its wiring
  - its routing
- Each run reports what it left untouched.

### 4.5 — The RF-simulation file

- Process 5 writes what `rf-simulation` reads.
- Three-dimensional geometry is carried on the KiCad User layers:
  - each layer names a vertical position and a height
  - the objects on it are the boxes at that level — dielectric or conductor

### 4.6 — One agent per process

- Each process is entered on its own and calls the tools its T15 row names.
- They are written in order — 1 and 2, then 3 through 6 — each agreed
  working before the next.
- A process that needs the User mid-run is a command — loaded into the
  running session.
- A process that runs headless is an agent — holding its own context and
  reporting at the end.

**T17 — Process forms**

| # | Process | Form |
|---|---|---|
| r1 | 1 — Update parts | Command |
| r2 | 2 — Update library | Agent. Asks when a datasheet withholds the pinout |
| r3 | 3 — Update schematic | Agent |
| r4 | 4 — Update board | Agent |
| r5 | 5 — Output | Agent |
| r6 | 6 — Source | Agent |

- Claude Code reads its assets from fixed paths — the folder is carried as a
  plugin and its files stay tool assets — T18.

**T18 — Plugin paths**

| # | Path | Holds |
|---|---|---|
| r1 | `.claude-plugin/plugin.json` | the plugin manifest |
| r2 | `.claude-plugin/marketplace.json` | the local marketplace entry |
| r3 | `commands/<process>.md` | one command per interactive process |
| r4 | `agents/<process>.md` | one agent per batch process |
| r5 | `skills/<name>/SKILL.md` | skills the commands and agents load |

- A fresh clone installs it once:

```
/plugin marketplace add ./tools/board-build
/plugin install board-build
```

## 5 — Tools

### 5.1 — What a tool is

- A tool is a document and a set of Python scripts.
- The agent follows the document and calls the scripts.
- Code the agent writes to cover a gap is declared — so the gap can be
  closed.
- A process uses one or more tools; a tool serves one or more processes.
- Each tool document opens with the assets it reads and writes.

### 5.2 — The tools

There are ten.

**T19 — The tools**

| # | Tool | Function | In | Out |
|---|---|---|---|---|
| r1 | `db-init` | Create the database and its tables | T4, T5, T7, T8, T9 | `board.db` |
| r2 | `table-read` | Show the record, one view per workflow step | `board.db` | Markdown on stdout |
| r3 | `lib-index` | Index the KiCad symbol libraries | the User's `.kicad_sym` files | `lib/kicad-index.json` |
| r4 | `copy-kicad-part` | Find a symbol for a part in the KiCad libraries | `board.db`, KiCad libraries | `<library>:<symbol>`, or `null` |
| r5 | `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| r6 | `symbol-draw` | Copy or draw a symbol into `lib/` | KiCad libraries, pins from `datasheet-read` | `lib/*.kicad_sym`<br>`parts_table` — `symbol`, `source` |
| r7 | `footprint-draw` | Copy or draw a footprint into `lib/` | KiCad libraries, package from `datasheet-read` | `lib/*.pretty`, `lib/3d/`<br>`parts_table` — `footprint`, `source` |
| r8 | `table-write` | Create or modify part | Record row | `board.db` — `parts_table`, `ref_table`, `aml_table` |
| r9 | `kicad-init` | Create the KiCad project from nothing | `board.db` | `*.kicad_pro`, `*.kicad_sch`, `sym-lib-table`, `lib/` |
| r10 | `kicad-update` | Push the record into the KiCad project | `board.db`, `lib/` | `*.kicad_sch`, `*.kicad_pcb` |

### 5.3 — Layout

- `board-build-tool.md` — the only document at the top level
- `tools/` — one `<tool>.md` and its scripts, per tool
- `.claude-plugin/` — `plugin.json`, `marketplace.json`
- `commands/` — one per interactive process, section 4.6
- `agents/` — one per batch process, section 4.6
- `skills/` — skills they load
