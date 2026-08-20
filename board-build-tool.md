# Board-build

A framework for agent-assisted hardware design: the processes that carry a
board through KiCad, the documents that define them, and the scripts they
call. It carries nothing specific to one product.

The board is designed by working the processes in order and revised by
re-entering them. The tool is entered at whichever process is next.

## Contents

1. [Introduction](#1--introduction)
2. [Assets](#2--assets)
3. [Processes](#3--processes)
4. [Tools](#4--tools)

## 1 — Introduction

**The split**

| Tool | User |
|---|---|
| the parts data, on the User's decisions | wiring |
| symbols, footprints, models | board outline and stackup |
| symbol placement, footprint assignment and placement | routing |
| the RF-simulation file | |

**The data**

The design keys to an **IPN**, an internal part number. An MPN hangs off an
IPN, and several MPNs may satisfy one.

Parameters flow database to design. A field is edited in the database and
pushed.

**T1.1 — Where the data lives**

| File | Holds |
|---|---|
| `board.db` | `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `offer_table` |
| `*.kicad_sch`, `*.kicad_pcb` | `Reference`, `Value`, `Footprint`, `ipn` |

`board.db` is master and pushes to KiCad. Table names end in `_table`; keys
carry the bare word.

Design reads `parts_table` and `ref_table`. Ordering reads `aml_table`,
`mpn_table` and `offer_table`. `aml_table` and `mpn_table` are kept;
`offer_table` is fetched and discardable.
Sources are the JLCPCB API and distributor tables the User supplies.

The tables are a record of the design. Progress is a query:

| Question | Query |
|---|---|
| has a symbol | `symbol is null` |
| has a footprint | `footprint is null` |
| has a part number | a row in `aml_table` |
| quantity | `count(*) from ref_table group by ipn` |
| the assembly | rows whose `parent` is this IPN |

`note` is a person's sentence, on `parts_table` and `aml_table`. Normally
blank. Tools write the columns.

**What is fixed and what is not**

The tables, their keys and the relations of T1.5 are the structure. They are
enforced.

An agent that finds it needs one of them changed has found a bug. It aborts,
declares the tool unusable, and names what it hit. It does not carry on, and
it does not ask.

The core tables are immutable. At runtime the User may add a field to any of
them at will.

A tool changes the schema on the User's request and on nothing else. Not to
carry a task, and never on an inference.

**T1.2 — The design tables**

`parts_table`

| Column | Type | Key | Null |
|---|---|---|---|
| `ipn` | TEXT | key | |
| `description` | TEXT | | yes |
| `parent` | TEXT | | yes |
| `symbol` | TEXT | | yes |
| `footprint` | TEXT | | yes |
| `source` | TEXT | | yes |
| `note` | TEXT | | yes |

`ref_table`

| Column | Type | Key | Null |
|---|---|---|---|
| `uuid` | TEXT | key | |
| `ipn` | TEXT | | |
| `ref` | TEXT | | yes |
| `page` | TEXT | | yes |
| `room` | TEXT | | yes |

`parts_table` is the part, one row per IPN. `ref_table` is the instance: `U1`
and `U2` are two rows on one `ipn`, each free to carry its own `page` and
`room`.

`uuid` is KiCad's instance UUID — the `.kicad_sch` symbol carries it and the
`.kicad_pcb` footprint holds `(path "/<sheet-uuid>/<symbol-uuid>")` back to
it.

`ref` is a field. Reannotation in the editor is read back into it.

`page` is the schematic page. `room` is the User's region, one name serving a
block of the sheet and a region of the board.

`parent` is an IPN: the part this one serves. A feedback resistor carries the
op-amp's IPN. Children are the assembly, the parent its primary part. Blank
is top level. One level.

`source` is two letters, symbol then footprint.

| Letter | Origin |
|---|---|
| `s` | KiCad stock libraries |
| `v` | manufacturer or a publishing service |
| `h` | drawn here, against the datasheet |
| `-` | absent |

`h/-` is a hand-drawn symbol and no footprint.

`symbol` and `footprint` are `<project>:<name>`, resolving inside `lib/` per
section 2. `symbol-draw` and `footprint-draw` copy the object in and write
the field. A KiCad library is an input to those tools; `source` records it as
the origin.

**The IPN**

`ANNNN` — class letter, four digits, sequential within the letter from
`0001`, one number to one part for the life of the design. Form, fit or
function change takes a new IPN; any other change is a second MPN in
`aml_table`.

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

Ten letters match the KiCad reference designator for the same class. Four
digits mark the IPN: `U0001` is a part, `U1` an instance. This table is the
class list; a new class is added here first.

**T1.4 — Where a thing is placed**

| Rank | Dimension | Schematic | Board |
|---|---|---|---|
| 1 | `page` | selects the file | — |
| 2 | `room` | block of the sheet | region of the board |
| 3 | family | groups instances whose `room` is blank | groups instances whose `room` is blank |

Placement follows this order. A family whose instances carry two pages is
placed on both.

Order of rooms and families within a page is arbitrary, subject to the
re-entry rule of T3.1. Order within a family is defined in the tool
documents.

**T1.5 — The relations**

| From | To | Cardinality | On delete |
|---|---|---|---|
| `ref_table.ipn` | `parts_table.ipn` | many-to-one | restrict |
| `parts_table.parent` | `parts_table.ipn` | many-to-one, self | set null |
| `aml_table.ipn` | `parts_table.ipn` | many-to-one | restrict |
| `aml_table.mpn` | `mpn_table.mpn` | many-to-one | restrict |
| `offer_table.mpn` | `mpn_table.mpn` | many-to-one | cascade |

All six are declared foreign keys. Every tool sets `PRAGMA foreign_keys = ON`.

**T1.3 — The sourcing tables**

`aml_table`

| Column | Type | Key | Null |
|---|---|---|---|
| `ipn` | TEXT | key | |
| `mpn` | TEXT | key | |
| `rank` | INTEGER | | yes |
| `note` | TEXT | | yes |

`mpn_table`

| Column | Type | Key | Null |
|---|---|---|---|
| `mpn` | TEXT | key | |
| `manufacturer` | TEXT | | yes |
| `datasheet` | TEXT | | yes |

`offer_table`

| Column | Type | Key | Null |
|---|---|---|---|
| `mpn` | TEXT | key | |
| `distributor` | TEXT | key | |
| `break_qty` | INTEGER | key | |
| `sku` | TEXT | | yes |
| `currency` | TEXT | | yes |
| `price` | REAL | | yes |
| `stock` | INTEGER | | yes |
| `moq` | INTEGER | | yes |
| `lead_days` | INTEGER | | yes |
| `fetched_at` | TEXT | | yes |

`aml_table` is the approved manufacturer list: the MPNs that may be built
against an IPN. The row is the approval, and every MPN in it takes the board
as designed.

`rank` orders the alternatives, blank on the one designed against. One blank
per IPN, held as a unique index over `ipn` where `rank is null`.

`mpn_table` is the manufacturer part: maker and datasheet. The row exists
from the moment the part number is named. Package, pin count and pitch are
read from the datasheet by `datasheet-read` and land in the footprint.

`offer_table` is fetched: one row per distributor per quantity break.
`fetched_at` dates it.

**What the schematic carries**

| Field | |
|---|---|
| `Reference`, `Value`, `Footprint` | built in |
| `ipn` | custom, the key to `parts_table` |

`Value` is drawn from the IPN.

## 2 — Assets

**T2.1 — The board project**

| Asset | Owner |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table` | Hand |
| `board.db` — `offer_table` | Fetched. Discardable |
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
KiCad install, the project opens and edits with nothing missing. Every
library, footprint and model resolves inside the repository.

- `sym-lib-table` and `fp-lib-table` sit in the project directory and are committed.
- Every path in them is `${KIPRJMOD}/lib/...`.
- Every model path in every `.kicad_mod` is `${KIPRJMOD}/lib/3d/...`.
- Nicknames are prefixed to the project, so a global entry on another
  machine cannot collide.
- `${KIPRJMOD}` is the only path variable, and every path is relative to it.
- Symbols, footprints and models are copied into `lib/`, and owned from that
  point.

The check runs against a fresh clone, and fails on any of the above.

## 3 — Processes

**T3.1 — The chain**

| # | Process | In | Out | Tools | User then |
|---|---|---|---|---|---|
| 0 | Init | The init state | `board.db`, five empty tables<br>`*.kicad_pro`<br>`sym-lib-table`<br>`lib/<project>.kicad_sym` | `db-init`<br>`kicad-init` | — |
| 1 | Update parts | Datasheet<br>Record row | `parts_table` row | `table-write` | — |
| 2 | Update library: symbol, footprint, 3D model | `board.db`<br>`datasheets/` | `lib/*.kicad_sym`<br>`lib/*.pretty`<br>`lib/3d/` | `datasheet-read`<br>`symbol-draw`<br>`footprint-draw` | — |
| 3 | Update schematic | `board.db`<br>`lib/*.kicad_sym` | `*.kicad_sch`<br>Symbols, on their page | `kicad-update` | Wires |
| 4 | Update board | `board.db`<br>`*.kicad_sch`<br>`lib/*.pretty`<br>`lib/3d/` | `*.kicad_pcb`<br>Footprints, placed | `kicad-update` | Routes |
| 5 | Output | `*.kicad_pcb` | RF-simulation file | — | — |
| 6 | Source | `aml_table` | Price<br>Stock<br>Availability | — | — |

Process 1 gives a part its IPN and its description. Process 2 builds the
library objects for the rows that lack them.

**The RF-simulation file**

Process 5 writes what `rf-simulation` reads. Three-dimensional geometry is
carried on the KiCad User layers: each layer names a vertical position and a
height, and the objects on it are the boxes at that level, dielectric or
conductor.

**Re-entry**

A process adds what is missing and leaves what is there. A placed part keeps
its position, its wiring and its routing. Each run reports what it left
untouched.

**The init state**

An empty board directory, and a pointer to the raw source the parts come
from. Nothing else: no `board.db`, no `lib/`, no KiCad files.

The board directory carries no product name and no reference of its own. The
source is named to the process that reads it, and lives outside the board
directory.

Every file in the board directory is made by a tool, so a full init deletes
all of them and starts from nothing.

**Process 0 — Init**

From the init state, three steps, in this order:

| # | Tool | Makes |
|---|---|---|
| 1 | `db-init` | `board.db` and its five empty tables |
| 2 | `table-write` | `parts_table`, `ref_table` and `aml_table`, from the reference |
| 3 | `kicad-init` | `*.kicad_pro`, `sym-lib-table`, `lib/<project>.kicad_sym` |

Steps 1 and 3 are process 0, step 2 is process 1. After them the board
directory holds the record and an empty project, and process 2 has somewhere
to put a symbol.

**T3.2 — One agent per process**

Each process is entered on its own and calls the tools its T3.1 row names.
They are written in order — 1 and 2, then 3, 4, 5, 6 — each agreed working
before the next.

A process that needs the User mid-run is a command, loaded into the running
session. A process that runs headless is an agent, holding its own context
and reporting at the end.

| # | Process | Form |
|---|---|---|
| 1 | Update parts | Command |
| 2 | Update library | Agent. Asks when a datasheet withholds the pinout |
| 3 | Update schematic | Agent |
| 4 | Update board | Agent |
| 5 | Output | Agent |
| 6 | Source | Agent |

Claude Code reads commands, agents and skills from fixed paths, so the folder
is carried as a plugin and its files stay tool assets:

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
document and calls the scripts, and declares any code it writes to cover a
gap so the gap can be closed.

A process uses one or more tools; a tool serves one or more processes. Each
tool document opens with the assets it reads and writes.

**T4.1 — The tools**

There are ten. A document, or a version of one, that carries eleven is a
bug: abort the run, declare the tool unusable, and report it.


| Tool | Function | In | Out |
|---|---|---|---|
| `db-init` | Create the database and its tables | T1.2, T1.3 | `board.db` |
| `table-read` | Show the record, one view per workflow step | `board.db` | Markdown on stdout |
| `lib-index` | Index the KiCad symbol libraries | the User's `.kicad_sym` files | `lib/kicad-index.json` |
| `copy-kicad-part` | Find a symbol for a part in the KiCad libraries | `board.db`, KiCad libraries | `<library>:<symbol>`, or `null` |
| `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| `symbol-draw` | Copy or draw a symbol into `lib/` | KiCad libraries, pins from `datasheet-read` | `lib/*.kicad_sym`<br>`parts_table` — `symbol`, `source` |
| `footprint-draw` | Copy or draw a footprint into `lib/` | KiCad libraries, package from `datasheet-read` | `lib/*.pretty`, `lib/3d/`<br>`parts_table` — `footprint`, `source` |
| `table-write` | Create or modify part | Record row | `board.db` — `parts_table`, `ref_table`, `aml_table` |
| `kicad-init` | Create the KiCad project from nothing | `board.db` | `*.kicad_pro`, `*.kicad_sch`, `sym-lib-table`, `lib/` |
| `kicad-update` | Push the record into the KiCad project | `board.db`, `lib/` | `*.kicad_sch`, `*.kicad_pcb` |

**Layout**

- `board-build-tool.md` — the only document at the top level
- `tools/` — one `<tool>.md` and its scripts, per tool
- `.claude-plugin/` — `plugin.json`, `marketplace.json`
- `commands/` — one per interactive process, T3.2
- `agents/` — one per batch process, T3.2
- `skills/` — skills they load
