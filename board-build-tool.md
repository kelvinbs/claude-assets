# Board-build

A framework for agent-assisted hardware design. It holds the processes that
carry a board through KiCad, the documents that describe them, and the
scripts they call. Nothing in it is specific to one product.

The board takes its specification from the processes themselves. It is
designed by working them in order, and revised by re-entering them — a part is added, a
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
handle for a part. An MPN is data hanging off an IPN, and several MPNs may
satisfy one.

Parameters flow one way, database to design. A field is edited in the
database and pushed.

**T1.1 — Where the data lives**

Two databases and the KiCad files. Every table name ends in `_table`, and a key
carries the bare word, so a table and its key read apart.

| File | Holds | Role |
|---|---|---|
| `board.db` | `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `lifecycle_table`, `offer_table` | Master for the design fields. Pushes to KiCad |
| `*.kicad_sch`, `*.kicad_pcb` | — | The native design store: `Reference`, `Value`, `Footprint`, `ipn` |

KiCad is the native store for design use, generally updated from the master.

One file, so that every relation of T1.5 is a declared foreign key.

A design process reads `parts_table` and `ref_table`. Ordering reads
`aml_table`, `mpn_table`, `lifecycle_table` and `offer_table`. The join
is `ipn`.

`lifecycle_table` and `offer_table` are fetched, and may be discarded and
fetched again. `aml_table` is an approval and is kept, and `mpn_table` is the
part number's identity and is kept with it.

Availability and price come from the JLCPCB API, the only distributor
interface that answers, or from distributor tables the User downloads and
hands over.

**A record**

These tables say what the design is.

Progress is a query against them, asked at the moment it matters:

| Question | Answer |
|---|---|
| has this part a symbol | `symbol is null` |
| has it a footprint | `footprint is null` |
| has it a part number | a row in `aml_table` |
| what does the board use | `count(*) from ref_table group by ipn` |
| what is this assembly | the rows whose `parent` is this IPN |

**`note`**

A person's sentence about a row, held by `parts_table`, `ref_table`,
`aml_table` and `mpn_table`. Why this part over the obvious one, what the
datasheet gets wrong, what to check before ordering again. A person writes
it, a person reads it, and blank is its normal state.

A tool records what it knows in the column that holds that fact.

**T1.2 — The design tables**

| Table | Key | Fields |
|---|---|---|
| `parts_table` | `ipn` | `description`, `category`, `parent`, `symbol`, `footprint`, `model`, `source`, `note` |
| `ref_table` | `uuid` | `ipn`, `ref`, `page`, `room`, `note` |

`parts_table` is the part. One row per IPN, whatever the board uses it for.

`ref_table` is the instance. `U1` and `U2` are two rows carrying one `ipn`,
and they are free to sit on different pages and in different rooms. A part
used forty times is one `parts_table` row and forty `ref_table` rows.

The key is the KiCad UUID, which is what KiCad keys an instance on. Every
symbol in a `.kicad_sch` carries one, and the footprint in the `.kicad_pcb`
carries `(path "/<sheet-uuid>/<symbol-uuid>")` back to it. It is the join the
files themselves use.

Quantity is `count(*) from ref_table group by ipn`.

`page` names the schematic page the symbol is placed on. `room` is the
User's tag for where the instance goes — a block of the sheet and a region of
the board, one name read by both. The User sets it. Both are properties of
the instance.

**T1.4 — Where a thing is placed**

Three dimensions, in this order. Both `sheet-place` and `board-place` obey
it, and neither has a rule of its own.

| Rank | Dimension | Schematic | Board |
|---|---|---|---|
| 1 | `page` | selects the file | — |
| 2 | `room` | which block of the sheet | which region of the board |
| 3 | family | groups what `room` has not already placed | groups what `room` has not already placed |

`page` selects the file the symbol is written into. A family whose instances
carry two pages is placed on both.

`room` outranks family. An instance with a `room` goes there.

Family is the default: a part and its children are placed together wherever
`room` is blank.

A KiCad group is what `board-place` writes when it puts a room or a family
together — a list of footprints that move as one.

The order of rooms and families within a page is arbitrary. A tool picks one,
and picks freely on the next run within the re-entry rule of T3.1: what is
already placed keeps its position.

The order of parts within a family is defined in the tool documents.

**`parent`**

An IPN, or blank. It names the part this one exists to serve.

A discrete takes its meaning from what it serves. The feedback resistor and
the feedback capacitor around an op-amp carry the op-amp's IPN, and so do its
bypass capacitors.

The parent names the function and is the primary part of it. Its children are
the assembly: the op-amp, the two feedback parts, the bypass capacitors. A
part with children is an assembly.

Blank is a top-level part. One level carries a board.

`parent` is the design's hierarchy. Placement is `page` and `room`, on the
instance, and they are the User's.

`source` records where the library objects came from. Two letters, the symbol
then the footprint, separated by a slash — `s/h` is a stock symbol with a
hand-drawn footprint.

| Letter | Came from |
|---|---|
| `s` | KiCad's own bundled libraries |
| `v` | the manufacturer, or a service that publishes for them |
| `h` | drawn here, against the datasheet |

Two letters because the two objects are chosen separately: a pinout is a
pinout, and a land pattern belongs to one part number.

`-` stands where an object is absent. `h/-` is a symbol drawn here, and a
footprint yet to come.

Section 2 copies every object into `lib/` and owns it from that point.
`source` is the record of where it came from.

**`ref` is a field, not a key**

Annotate and reannotate in the editor as you please. `ref` is read back off
the sheets and updated, and the UUID holds, so everything that points at an
instance still finds it.

The instance row exists before the symbol is placed — `page` is on the
instance, and `sheet-place` has nothing to read otherwise. The tool mints the
UUID at that point and writes it into the sheet it creates.

**The IPN**

`ANNNN`. One letter for the part class, from the list below, then four
digits. The digits are sequential inside that letter, from `0001`. Each
number belongs to one part for the life of the design.

The letter and the digits are the whole of it: the database is the project,
and the IPN is the part within it. A change of form, fit or function takes a
new IPN. Any other change is a second MPN against the same one, held in
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

Ten of them are the KiCad reference-designator letter for the same thing, so
the letter reads as itself to anyone who has opened a schematic. The four
fixed digits mark an IPN as one: `U0001` is a part, `U1` an instance.

`category` carries the class word. A new class is added to this list first.

**T1.5 — The relations**

Six, and every one is declared.

| From | To | On delete |
|---|---|---|
| `ref_table.ipn` | `parts_table.ipn` | restricted |
| `parts_table.parent` | `parts_table.ipn` | set null |
| `aml_table.ipn` | `parts_table.ipn` | restricted |
| `aml_table.mpn` | `mpn_table.mpn` | restricted |
| `lifecycle_table.mpn` | `mpn_table.mpn` | cascade |
| `offer_table.mpn` | `mpn_table.mpn` | cascade |

`parts_table.ipn` and `mpn_table.mpn` are the two hubs — the part you
designed and the part number you buy. `aml_table` is the only table that
touches both, and that is what it is for.

Foreign keys in SQLite are off unless a connection turns them on, so every
tool sets `PRAGMA foreign_keys = ON` before it writes.

A part is deleted once its instances and its approvals are gone: the
instances sit on a sheet, and an approval is a decision.

Deleting a parent leaves its children, with `parent` cleared.

A manufacturer part is deleted once no approval names it, and takes its
lifecycle and its offers with it. Discarding a fetch is exactly that.

**T1.3 — The sourcing tables**

| Table | Key | Fields |
|---|---|---|
| `aml_table` | `ipn` + `mpn` | `rank`, `note` |
| `mpn_table` | `mpn` | `manufacturer`, `package`, `pin_count`, `pitch_mm`, `datasheet`, `note` |
| `lifecycle_table` | `mpn` | `lifecycle`, `fetched_at` |
| `offer_table` | `mpn` + `distributor` + `break_qty` | `sku`, `currency`, `price`, `stock`, `moq`, `lead_days`, `fetched_at` |

`aml_table` is the approved manufacturer list: which MPNs may be built
against an IPN, ranked. The row is the approval. Every MPN in it drops in —
it takes the board as designed.

`mpn_table` is the manufacturer part itself: who makes it, what package it
comes in, how many pins, at what pitch, and the datasheet. It is kept. The
row exists from the moment the part number is named, with the fields filled
in as they become known.

`lifecycle_table` and `offer_table` hold what a fetch found — whether the
manufacturer still makes it, what a distributor charges, what is on the
shelf. Each is discarded and fetched again, and `fetched_at` says when it was
true.

An offer is one distributor's listing of one MPN at one quantity break. One
MPN carries many. Lifecycle is per part number, and holds one row per MPN.

`rank` orders the alternatives, and is blank on the one you designed against.
A number appears where there is something to order. One row per IPN carries
the blank, which the database holds as a unique index over `ipn` where
`rank is null`.

**What the schematic carries**

- `Reference`, `Value` and `Footprint`, built-in fields
- `ipn`, a custom field, the key back to `parts_table`

`Value` is drawn from the IPN. Which of `ipn.description` or the ranked
`aml_table` MPN fills it is defined in `sheet-place.md`.

## 2 — Assets

**T2.1 — The board project**

| Asset | Owner |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table` | Hand |
| `board.db` — `lifecycle_table`, `offer_table` | Fetched. Discardable |
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

A process takes the output of the one before it. Each uses one or more of
the tools in section 4.

| # | Process | In | Out | Tools | User then |
|---|---|---|---|---|---|
| 1 | Update parts | Datasheet<br>Record row | `parts_table` row | `table-write` | — |
| 2 | Update library | `board.db`<br>`datasheets/` | `lib/*.kicad_sym`<br>`lib/*.pretty`<br>`lib/3d/` | `datasheet-read`<br>`symbol-draw`<br>`footprint-draw` | — |
| 3 | Update schematic | `board.db`<br>`lib/*.kicad_sym` | `*.kicad_sch`<br>Symbols, on their page | `sheet-place` | Wires |
| 4 | Update board | `board.db`<br>`*.kicad_sch`<br>`lib/*.pretty` | `*.kicad_pcb`<br>Footprints, placed | `board-place` | Routes |
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
its own context and reporting at the end. A subagent runs headless, which is
what places each process on one side or the other.

| # | Process | Form |
|---|---|---|
| 1 | Update parts | Command |
| 2 | Update library | Agent. Asks when a datasheet withholds the pinout |
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
| `db-init` | Create the database and its tables | T1.2, T1.3 | `board.db` |
| `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| `symbol-draw` | Create or modify symbol | Pins from `datasheet-read` | `lib/*.kicad_sym` |
| `footprint-draw` | Create or modify footprint | Package from `datasheet-read` | `lib/*.pretty`, `lib/3d/` |
| `table-write` | Create or modify part | Record row, `datasheet-read` | `board.db` — `parts_table`, `ref_table`, `aml_table` |
| `sheet-place` | Place symbols on their page | `board.db`, `lib/*.kicad_sym` | `*.kicad_sch` |
| `board-place` | Place footprints on the board | `board.db`, `*.kicad_sch`, `lib/*.pretty` | `*.kicad_pcb` |
| `layer-export` | Export the board geometry as boxes | `*.kicad_pcb` | `out/` — the RF-simulation file |
| `stock-query` | Fetch price, stock and lifecycle | `board.db` — `aml_table`, JLCPCB API | `board.db` — `lifecycle_table`, `offer_table` |
| `clone-check` | Prove a fresh clone opens with nothing missing | A fresh clone of the project | A verdict |

**Layout**

- `board-build-tool.md` — the only document at the top level
- `tools/` — one `<tool>.md` and its scripts, per tool
- `.claude-plugin/` — `plugin.json`, `marketplace.json`
- `commands/` — one per interactive process, T3.2
- `agents/` — one per batch process, T3.2
- `skills/` — skills they load

**State**

- Written: this document, `tools/db-init.md`, `tools/table-write.md`.
- Built: `tools/db-init.py`, `tools/table-write.py`.
- `builds/proto1/tools` is the working precedent for the rest, and has not
  been moved here.
