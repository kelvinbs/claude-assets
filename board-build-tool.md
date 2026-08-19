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
| `board.db` | `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `lifecycle_table`, `offer_table` | Master for the design fields. Pushes to KiCad |
| `*.kicad_sch`, `*.kicad_pcb` | — | The native design store: `Reference`, `Value`, `Footprint`, `ipn` |

KiCad is the native store for design use, generally updated from the master.

One file, because a foreign key cannot cross two. The design tables and the
sourcing tables were split into two files once, and that split
enforced nothing — it only made `aml_table.ipn` impossible to declare. The
rule it was meant to carry survives as a rule: a design process does not read
`aml_table`, `mpn_table` or `offer_table`, and ordering writes none of the
others. The join is `ipn`.

`lifecycle_table` and `offer_table` are fetched, never typed, and may be
discarded and fetched again. `aml_table` is an approval and is kept, and
`mpn_table` is the part number's identity and is kept with it.

Availability and price come from the JLCPCB API, the only distributor
interface that answers, or from distributor tables the User downloads and
hands over.

**A record, not a tracker**

These tables say what the design is. They do not say what has been done to
it, who did it, or what is left.

Progress is not a field. Whether a part has a symbol is `symbol is null`.
Whether it has a footprint is `footprint is null`. Whether it has a part
number is whether `aml_table` holds a row for it. A field that restates one
of those is a second place for it to be wrong, and the two disagree the first
time somebody writes one and not the other.

So there is no `status`, no `pins_checked`, no `approved`. What a process
still owes is read off the record, in one query, at the moment it is asked.

**`note`**

Every table carries one, and it is normally blank.

It is for a person to write a sentence a column cannot hold — why this part
and not the obvious one, what the datasheet gets wrong, what to check before
ordering again. It is read by a person and by nothing else.

A tool does not write it. Not where a value came from, not which document a
row was imported out of, not when it was touched — a tool that has something
to say about a row says it in the column that holds that fact, or does not
say it. A `note` full of machine bookkeeping is a `note` nobody reads, and
the one sentence that mattered is lost in it.

Blank is the normal state.

**T1.2 — The design tables**

| Table | Key | Fields |
|---|---|---|
| `parts_table` | `ipn` | `description`, `category`, `parent`, `symbol`, `footprint`, `model`, `source`, `note` |
| `ref_table` | `uuid` | `ipn`, `ref`, `page`, `room`, `note` |

`parts_table` is the part. One row per IPN, whatever the board uses it for.

`ref_table` is the instance. `U1` and `U2` are two rows carrying one `ipn`,
and they are free to sit on different pages and in different rooms. A part
used forty times is one `parts_table` row and forty `ref_table` rows.

The key is the KiCad UUID, because that is what KiCad already keys an
instance on. Every symbol in a `.kicad_sch` carries one, and the footprint in
the `.kicad_pcb` carries `(path "/<sheet-uuid>/<symbol-uuid>")` back to it.
It is the join the files themselves use, and keying on anything else would
mean storing it as well.

There is no `qty` field. Quantity is `count(*) from ref_table group by ipn`,
and a field would only be a second place for it to be wrong.

`page` names the schematic page the symbol is placed on. `room` is the
User's tag for where the instance goes — a block of the sheet and a region of
the board, one name read by both. No tool invents one. Both are properties of
the instance, not of the part.

**T1.4 — Where a thing is placed**

Three dimensions, in this order. Both `sheet-place` and `board-place` obey
it, and neither has a rule of its own.

| Rank | Dimension | Schematic | Board |
|---|---|---|---|
| 1 | `page` | selects the file | — |
| 2 | `room` | which block of the sheet | which region of the board |
| 3 | family | groups what `room` has not already placed | groups what `room` has not already placed |

`page` is not a preference. It selects the file the symbol is written into,
and there is nowhere else to put it, so a family split across two pages stays
split.

`room` outranks family. An instance with a `room` goes there, and it leaves
its family's block to do it. Nothing is reported — it was set by hand and the
hand meant it.

Family is the default. A part and its children are placed together wherever
`room` has not already spoken, which today is every instance.

A KiCad group is what `board-place` writes when it puts a room or a family
together. It is a list of footprints that move as one, and nothing more.

The order of rooms and families within a page is arbitrary. Nothing in the
record ranks them and nothing needs to — a sheet is read by what is on it,
not by what came first. A tool picks an order, and is free to pick a
different one next run as long as what is already placed stays where it is.

The order of parts within a family belongs to the tool documents.

**`parent`**

An IPN, or blank. It names the part this one exists to serve.

A discrete is not a part on its own — a 10 k resistor is nothing until you
say which loop it closes. `parent` says it. The feedback resistor and the
feedback capacitor around an op-amp carry the op-amp's IPN, and so do its
bypass capacitors.

The parent names the function and is the primary part of it. Ask for its
children and what comes back is the assembly: the op-amp, the two feedback
parts, the bypass capacitors. Nothing has to be declared an assembly — a part
with children is one, and a part with none is not.

Blank is a top-level part. One level of parent is enough for a board; a
parent that is itself a child is not refused, but nothing here needs it.

`parent` is the design's own hierarchy and is not placement. Placement is
`room`, on the instance, and it is the User's.

`source` records where the library objects came from. Two letters, the symbol
then the footprint, separated by a slash — `s/h` is a stock symbol with a
hand-drawn footprint.

| Letter | Came from |
|---|---|
| `s` | KiCad's own bundled libraries |
| `v` | the manufacturer, or a service that publishes for them |
| `h` | drawn here, against the datasheet |

They are recorded apart because they are chosen apart. A stock symbol is
usually fine — a pinout is a pinout. A stock footprint for a specific part
number rarely is, and the two are almost never taken from the same place.

Where a part has only one of the two, the other letter is `-`: `h/-` is a
symbol drawn here and no footprint yet.

Once a symbol is copied into `lib/` under the clone rule of section 2,
nothing else distinguishes a copied stock symbol from a drawn one, which is
why this field exists at all.

**`ref` is a field, not a key**

Annotate and reannotate in the editor as you please. `ref` is read back off
the sheets and updated; the UUID does not move, so nothing that points at an
instance is disturbed.

The instance row exists before the symbol is placed — `page` is on the
instance, and `sheet-place` has nothing to read otherwise. The tool mints the
UUID at that point and writes it into the sheet it creates.

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

**T1.5 — The relations**

Six, and every one is declared. There are no exemptions. A relation that is
real and cannot be declared means a table is wrong, and the table gets fixed.

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

Deleting a part that still has instances is refused — the instances are on a
sheet. Deleting a part that is approved against a manufacturer part is
refused too: the approval is a decision, and a decision does not evaporate
because a row was removed. Drop the approval first.

Deleting a parent leaves its children with `parent` cleared. They are still
parts, they have simply lost the thing they served.

Deleting a manufacturer part is refused while an approval names it, and
takes its lifecycle and its offers with it once none does. That is what
discarding a fetch means, and it is why the fetched tables cascade and the
approval does not.

**T1.3 — The sourcing tables**

| Table | Key | Fields |
|---|---|---|
| `aml_table` | `ipn` + `mpn` | `rank`, `note` |
| `mpn_table` | `mpn` | `manufacturer`, `package`, `pin_count`, `pitch_mm`, `datasheet`, `note` |
| `lifecycle_table` | `mpn` | `lifecycle`, `fetched_at` |
| `offer_table` | `mpn` + `distributor` + `break_qty` | `sku`, `currency`, `price`, `stock`, `moq`, `lead_days`, `fetched_at` |

`aml_table` is the approved manufacturer list: which MPNs may be built
against an IPN, ranked. The row is the approval — a part number that may not
be built is not in the table, and one that would need the board changed to
take is not either. Every MPN in it drops in, or it is not in it.

`mpn_table` is the manufacturer part itself. Who makes it, what package it
comes in, how many pins, at what pitch, and the datasheet. None of that
changes, none of it is fetched from anywhere in particular, and none of it is
thrown away. The row exists from the moment the part number is named — with
every field but the key empty, if that is all that is known yet.

`lifecycle_table` and `offer_table` are what a fetch found. Whether the
manufacturer still makes it, what a distributor charges, what is on the
shelf. All of it goes stale, all of it is discarded and fetched again, and
`fetched_at` says when it was true.

The split is the point. An approval names a part number and must outlive
every fetch; a price must not. Keeping identity and fetched state in one
table forced the approval to depend on a fetch, and the last two versions of
this document wrote an exemption instead of the split.

An offer is one distributor's listing of one MPN at one quantity break. One
MPN carries many. Lifecycle is per part number, not per distributor, so it is
its own table and not a column on the offer.

`rank` orders the alternatives, and is blank on the one you designed against.
A number appears only when there is something to order — an IPN with one
approved part number has one row and nothing to say about it. At most one row
per IPN may be blank, which the database holds as a unique index over `ipn`
where `rank is null`.

**What the schematic carries**

- `Reference`, `Value` and `Footprint`, all built in
- `ipn`, the key back to `parts_table`, which is not

`Value` is drawn from the IPN, not from a manufacturer part number — which of
`ipn.description` or the ranked `aml_table` MPN fills it is not yet settled.

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
