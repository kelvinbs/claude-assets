# Board-build

## Contents

1. [Introduction](#1--introduction)
2. [Data](#2--data)
3. [The project folder](#3--the-project-folder)
4. [The pipeline](#4--the-pipeline)
5. [Skills](#5--skills)

## 1 — Introduction

### 1.1 — What it is

- Claude-assisted hardware design in KiCad.
- The tool carries:
  - The stages that carry a project through KiCad
  - The documents that define them
  - The scripts they call
- Nothing specific to one product.

### 1.2 — How it is used

- The project is designed by working the stages of T4.1 in order — as often as
  needed.
- Revision re-enters a stage.
- The tool is entered at whichever stage is next.

### 1.3 — How it works

- The LLM performs the stages using the seven skills — T5.1.

### 1.4 — The contract

- The structure — enforced and immutable:
  - The stages — T4.1
  - The seven skills — T5.1
  - The four tables with their keys — sections 2.3, 2.4 and 2.9
  - The relations — T2.9
- The tool is stateless.
- A field may be added to any table at runtime. Other schema change is a
  change to this document.
- No code or skill outside T5.1. Authoring scripts at runtime is
  prohibited.
- ERC and DRC: the tool does not run the checks, but may set up the
  checks to be performed.
- On a failure — or when a skill does not cover the work:
  - Abort the stage
  - Report the bug
  - The tool is unusable until fixed
  - The runtime agent is not authorized to fix the tool

### 1.5 — How it is organized

- The tool lives in `tools/board-build/`.
- Work files live in the project folder.

## 2 — Data

### 2.1 — The key

- The design keys to an **IPN** — an internal part number.
- The MPN is a column on the part — a prototype buys one part one way.
- Parameters flow database to design: a field is edited in the database and
  pushed.

### 2.2 — Where the data lives

**T2.1 — Where the data lives**

| # | File | Holds |
|---|---|---|
| 1 | `board.db` | `project_table`, `parts_table`, `ref_table`, `price_table`, `net_table`, `bus_table` |
| 2 | `lib/<project>.kicad_sym` | per IPN: `Value`, `Footprint`, `Description`, `Datasheet`, `Manufacturer`, `MPN`, `note`, `ipn` |
| 3 | `*.kicad_sch`, `*.kicad_pcb` | `Reference`, `ipn`; on the board, each footprint's sheet path, rewritten on push |

- `board.db` is master. Part fields push to the library symbol; instance
  data pushes to the sheet; pull reads library fields back into the
  record.
- The schematic returns an instance the User placed on it — its existence
  and its `Reference`, nothing else. The tool deletes on neither side.
- Instances take the record's fields on push, as the library symbol does.
- Table names end in `_table`; keys carry the bare word.
**T2.2 — Progress queries**

| # | Question | Query |
|---|---|---|
| 1 | Has a symbol | `symbol is null` |
| 2 | Has a footprint | `footprint is null` |
| 3 | Has a part number | `mpn is not null` |
| 4 | Quantity | `count(*) from ref_table group by ipn` |
| 5 | The assembly | Rows whose `parent` is this IPN |

### 2.3 — The design tables

**T2.3 — `parts_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `ipn` | TEXT | Key |  |
| 2 | `description` | TEXT |  | Yes |
| 3 | `value` | TEXT |  | Yes |
| 4 | `symbol` | TEXT |  | Yes |
| 5 | `footprint` | TEXT |  | Yes |
| 6 | `source` | TEXT |  | Yes |
| 7 | `note` | TEXT |  | Yes |
| 8 | `name` | TEXT | Unique | Yes |
| 9 | `mpn` | TEXT |  | Yes |
| 10 | `manufacturer` | TEXT |  | Yes |
| 11 | `datasheet` | TEXT |  | Yes |
| 12 | `checked` | TEXT |  |  |
| 13 | `pinout_checked` | TEXT |  |  |

- `checked` is the User's sign-off on the part. `pinout_checked` is the
  User's mark that the part file's pins were read against the datasheet.
  Both default to `no`. The tool never sets either.

**T2.4 — `ref_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `uuid` | TEXT | Key | |
| 2 | `ipn` | TEXT | | |
| 3 | `parent` | TEXT | | Yes |
| 4 | `ref` | TEXT | | Yes |
| 5 | `page` | TEXT | | Yes |
| 6 | `room` | TEXT | | Yes |
| 7 | `unit` | INTEGER | | Yes |
| 8 | `path` | TEXT | Key | |
| 9 | `parent_path` | TEXT | | Yes |

- An instance is `(uuid, path)`: the symbol drawn on a sheet, in one
  instance of that sheet. `uuid` is the symbol's uuid in the sheet file.
  `path` is `''` on a root page; in a sub-sheet it is the chain of
  sheet-instance uuids from the root page down, `/`-joined.
- A sub-sheet is a part of class `B` (T2.10). Its instances are rows of
  that part, drawn as sheet symbols on the page they name. Every symbol
  drawn in the sub-sheet is one drawing and one row per instance of the
  sheet, each row with its own `ref`.
- `parent` with `parent_path` names the parent instance. A parent in the
  same sub-sheet is matched instance for instance.

**T2.5 — `source` letters**

| # | Letter | Origin |
|---|---|---|
| 1 | `s` | KiCad stock libraries |
| 2 | `v` | Manufacturer or a publishing service. LCSC, through `copy-kicad-part --lcsc` |
| 3 | `h` | Drawn here, against the datasheet |
| 4 | `-` | Absent |

### 2.4 — The price survey

- The sourcing tables were removed 2026-09-02 (n0.4). Vendor identity lives
  on the part — `mpn`, `manufacturer`, `datasheet` in T2.3.
- What a part costs is not one number. A vendor quotes a ladder, and the
  build size decides which rung applies. `price_table` holds the ladder, so
  the survey is taken once and any build size reads off it.

**T2.6 — `price_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `ipn` | TEXT | Key | |
| 2 | `vendor` | TEXT | Key | |
| 3 | `vendor_pn` | TEXT | Key | |
| 4 | `break_qty` | INTEGER | Key | |
| 5 | `unit_price` | REAL | | Yes |
| 6 | `stock` | INTEGER | | Yes |
| 7 | `checked` | TEXT | | Yes |

- One row per vendor break. The key is the part, the vendor, the vendor
  part number and the break, so a re-survey overwrites the rung it re-quotes
  and leaves the rest.
- A vendor that quotes two ladders side by side, cut tape and reel, gives
  each its own vendor part number. That is what separates them.
- `checked` dates the survey. A price with no date is not a price.
- Quantity per board is not stored. It is `ref_table` count, and units are
  that count times the number of boards.

**T2.6a — `net_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `uuid` | TEXT | Key | |
| 2 | `pin` | TEXT | Key | |
| 3 | `net` | TEXT | | |

- One row per drawn pin that carries a net name. A pin with no row
  carries no label. The drawing, not the part: two drawings of one part
  sit on different nets. A drawing in a sub-sheet has one set of nets for
  every instance of the sheet — a label is drawn once in the file.
- A sheet instance has pins too: the nets its sub-sheet exports, named by
  the pin. A row on a sheet instance names what the pin joins on the page
  it sits on. A bus pin is named `{BUS}`.
- `kicad-update` writes a label at the pin end from it, on place and on
  push — a local `label`, or a `hierarchical_label` when the net leaves
  the sheet file (T2.6c). Never a global label. Push deletes every label
  on every pin end of every record drawing first, so a moved or cleared
  net leaves nothing behind.
- A label typed by hand on a record part's pin lasts until the next push.

**T2.6b — `bus_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `net` | TEXT | Key | |
| 2 | `bus` | TEXT | | |

- A net is in at most one bus. Members keep their names; the bus is a
  group alias, `{BUS}`, written to the project file's
  `schematic.bus_aliases` by `kicad-update`.

**T2.6c — What a net is on a sheet**

| # | The net | On the sheet file | On the sheet symbol above |
|---|---|---|---|
| 1 | in this file only, or only here and in the sub-sheets under it | local `label` at the pin | nothing |
| 2 | also outside this file and what is under it, not in a bus | local `label` at the pin; a port in the port area: `hierarchical_label`, stub, local `label` of the same name | one pin, that name |
| 3 | in a bus | local `label` at the pin, the member's name; the bus breakout in the port area joins it | one pin per bus that leaves, `{BUS}` |
| 4 | named as a pin on an instance of this sub-sheet | as row 2 | that pin |

- A pin carries a local label and nothing else. A hierarchical label
  sits only in the port area.
- The port area: one per file, the tool's, at the top right, a column
  and then another to its left. Every port and every bus breakout of the
  file sits there, redrawn there on every run. A bus leaves a file when a
  member does; its breakout is the hierarchical bus label, the bus, one
  entry per member the file uses, ending in a local label.
- The root joins its sheet symbols: every pin gets a stub and a root
  label, wire and label for a net, bus and bus label for a bus. The root
  is the tool's and is rewritten on every run; it keeps each page's sheet
  uuid and page number.

### 2.5 — The relations

**T2.9 — The relations**

| # | From | To | Cardinality | On delete |
|---|---|---|---|---|
| 1 | `ref_table.ipn` | `parts_table.ipn` | Many-to-one | Restrict |
| 2 | `ref_table.parent`, `ref_table.parent_path` | `ref_table.uuid`, `ref_table.path` | Many-to-one, self | Set null |
| 3 | `price_table.ipn` | `parts_table.ipn` | Many-to-one | Restrict |
| 4 | `net_table.uuid` | `ref_table.uuid` | Many-to-one | `table-write` removes the nets with a drawing's last row |
| 5 | `bus_table.net` | `net_table.net` | Many-to-one, by name | none — a member with no pin yet is allowed |

- 1 to 3 are declared foreign keys. 4 cannot be declared: `ref_table`'s
  key is `(uuid, path)` and a net belongs to the drawing, every path at
  once.
- Every skill sets `PRAGMA foreign_keys = ON`.

### 2.6 — The IPN

- `ANNNN` — class letter then four digits:
  - Sequential within the letter from `0001`
  - One number to one part for the life of the design
- A new IPN on change of:
  - Form
  - Fit
  - Function
- Any other change is an edit of the part's `mpn`.

**T2.10 — The IPN classes**

| # | Letter | Class |
|---|---|---|
| 1 | `A` | Amplifier |
| 1a | `B` | Block — a sub-sheet, drawn once, instanced where placed. Its `name` is the page it is drawn on; its `symbol` is `sheet:<name>`; its instances annotate as `SH` |
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
- `U0001` is a part, `U1` an instance.
- A multi-unit package is one instance: one row per unit, same `ref`,
  own uuid, `unit` numbering them from 1. Every unit is placed and every
  pin is visible — no hidden pins.

### 2.7 — What the KiCad project carries

**T2.11 — KiCad project fields**

| # | Field | Lives on | Record column |
|---|---|---|---|
| 1 | `Reference` | the instance | `ref_table.ref` |
| 2 | `Value` | the library symbol | `parts_table.value` |
| 3 | `Footprint` | the library symbol | `parts_table.footprint` |
| 4 | `Description` | the library symbol | `parts_table.description` |
| 5 | `Datasheet` | the library symbol | `parts_table.datasheet` |
| 6 | `Manufacturer` | the library symbol | `parts_table.manufacturer` |
| 7 | `MPN` | the library symbol | `parts_table.mpn` |
| 8 | `note` | the library symbol | `parts_table.note` |
| 9 | `ipn` | both | `parts_table.ipn`, the key |
| 10 | `parent` | the instance | `ref_table.parent`, written as that instance's `ref`; in a sub-sheet, every instance's parent, space-separated |
| 11 | `room` | the instance | `ref_table.room` |
| 12 | `label` or `hierarchical_label` | the drawing's pin end | `net_table.net`, kind per T2.6c |
| 12a | sheet symbol, its pins, the stubs and labels on them | the page a sub-sheet instance sits on | `ref_table` rows of the class-`B` part; pins per T2.6c; labels from `net_table` |
| 12b | bus breakout | each file a bus leaves | `bus_table` |
| 12c | `schematic.bus_aliases` | `<project>.kicad_pro` | `bus_table` |
| 12d | the root sheet | `<project>.kicad_sch` | the root pages and T2.6c |
| 12 | `checked` | the library symbol | `parts_table.checked`. The User's field. The tool writes it out and never sets it |

- `pinout_checked` stays in the record. It is not a project field and does
  not reach the library or the sheets.

- `parent` and `room` are per-instance and never reach the library
  symbol. They say what a part serves and which sub-circuit it sits in, so
  an engineer reads the organisation off the page.
- `Value` is `parts_table.value`, what a person reads on a sheet. The IPN
  is the key and travels in its own field.

### 2.8 — Where a thing is placed

**T2.12 — Placement**

| # | Rank | Dimension | Schematic | Board |
|---|---|---|---|---|
| 1 | 1 | `page` | Selects the file — a root page, or a sub-sheet | — |
| 2 | 1a | `path` | Which instance of a sub-sheet. Derived, never chosen: every instance of the sheet | — |
| 3 | 2 | `room` | Block of the sheet | Region of the board |
| 4 | 3 | `parent` | Groups a parent with its children | Groups a parent with its children |

- Order of rooms within a page is arbitrary — subject to re-entry,
  section 4.3.
- A sub-sheet page is drawn once, in its own file, whatever the number of
  instances. The instances sit on the page that placed the class-`B` part,
  as sheet symbols. KiCad's multichannel tools carry one instance's layout
  to the others on the board.

### 2.9 — The project name

**T2.13 — `project_table`**

| # | Column | Type | Key | Null |
|---|---|---|---|---|
| 1 | `name` | TEXT | Key | |

- One row, written at first init from the project folder name — the
  parent of `design/`; thereafter the database is master and the tools
  read it, never derive it.

## 3 — The project folder

### 3.1 — Folder structure

**T3.1 — Folder structure**

| # | Path | Holds |
|---|---|---|
| 1 | `<project>/` | the root. Its name is the project name |
| 2 | `design/` | the design folder — everything below lives in it |
| 3 | `design/board.db` | the record |
| 4 | `design/<project>.kicad_pro`, `design/<project>.kicad_sch`, `design/<project>.kicad_pcb` | the KiCad project |
| 5 | `design/sym-lib-table`, `design/fp-lib-table` | library resolution, committed |
| 6 | `design/lib/` | symbols, footprints, `3d/` models |
| 7 | `design/datasheets/` | manufacturer datasheets |
| 8 | `design/parts/` | part files, `<IPN>-<name>.json` — datasheet facts and copy provenance: `pins`, `units`, `symbol_donor`, `pages` at stage 3; `package`, `package_dims`, `footprint_donor` at stage 5. Keys per `datasheet-read.md` T1 |
| 9 | `design/out/` | generated exports |

### 3.2 — Assets

**T3.2 — Assets**

| # | Asset | Owner |
|---|---|---|
| 1 | `board.db` | Hand |
| 2 | `lib/*.kicad_sym` | Hand for graphics; the tool writes the fields |
| 3 | `lib/*.pretty` | Hand |
| 4 | `lib/3d/` | Hand |
| 5 | `datasheets/` | Hand |
| 6 | `*.kicad_pro` | Generated once; `schematic.bus_aliases` rewritten by the tool |
| 7 | `*.kicad_sch` | Updated by the tool, which does not wire. The root is the tool's, rewritten every run |
| 8 | `*.kicad_pcb` | Updated by the tool, which does not route |
| 9 | Board setup — stackup, fabricator rules, DRC rules | Hand |
| 10 | `out/` — RF-simulation file | Generated |
| 11 | `lib/kicad-lib-index.json` | Generated |

### 3.3 — No dependencies — clone and work

- KiCad libraries are a source to copy from — never a reference.
- `sym-lib-table` and `fp-lib-table` sit in the project folder and are
  committed.
- Every path in them is `${KIPRJMOD}/lib/...`.
- Every model path in every `.kicad_mod` is `${KIPRJMOD}/lib/3d/...`.
- Nicknames are prefixed to the project, so a global entry on another
  machine cannot collide.
- `${KIPRJMOD}` is the only path variable, and every path is relative to it.
- Symbols, footprints and models are copied into `lib/`, and owned from that
  point.
- The check runs against a fresh clone, and fails on any of the above.

## 4 — The pipeline

### 4.1 — The stages

**T4.1 — The stages**

| # | Stage | Skills | Outside the tool |
|---|---|---|---|
| 1 | `init-pipeline` | `init-pipeline` | — |
| 2 | Update parts | `table-write` | — |
| 3 | Update library — symbols | `copy-kicad-part`<br>`datasheet-read` | — |
| 4 | Update schematic | `kicad-update` | Wires |
| 5 | Update library — footprints, 3D | `copy-kicad-part`<br>`datasheet-read` | — |
| 6 | Update PCB | `kicad-update` | Routes |
| 7 | RF-sim export | — | — |
| 8 | Source | — | — |

- Both Update library stages — symbols, footprints, 3D: `copy-kicad-part`
  is primary. Borrow, not build.
- On miss it returns `null` — the stage reports and stops.

### 4.2 — init-pipeline

One skill, one run — T4.2.

**T4.2 — init-pipeline makes**

| # | Makes |
|---|---|
| 1 | `board.db` and its six tables |
| 2 | `*.kicad_pro`, `*.kicad_sch`, `*.kicad_pcb`, `sym-lib-table`, `fp-lib-table`, `lib/<project>.kicad_sym`, `lib/<project>.pretty/` |

- The project folder names the project — its name is stored at first
  init (T2.13) and project filenames take it. The files live in
  `design/`, one level below the project root, and the name is the
  root's, never the design folder's.
- After it the project folder holds the record and an empty project —
  Update library has somewhere to put a symbol. `table-write` then loads
  the reference in Update parts.

### 4.3 — Re-entry

- A stage adds what is missing and leaves what is there.
- A placed part keeps:
  - Its position
  - Its wiring
  - Its routing
- Each run reports what it left untouched.
- A symbol the User placed on a sheet enters `ref_table` under its own
  uuid, with its `Reference`. No other field returns from a sheet.
- Push rewrites library-symbol and instance fields from the record; pull
  reads library fields back into it. Place leaves a placed instance's
  fields alone; push refreshes them.

### 4.4 — The RF-simulation file

- RF-sim export writes what `rf-simulation` reads.
- Three-dimensional geometry is carried on the KiCad User layers:
  - Each layer names a vertical position and a height
  - The objects on it are the boxes at that level — dielectric or conductor

### 4.5 — A stage is its skills

- A stage runs its skills in T4.1 order on the project folder and reports
  at the end.
- A stage does not prompt for missing information — it processes what it
  can and reports the omissions.

## 5 — Skills

### 5.1 — What a skill is

- A skill is LLM-based: defined by one document and zero or one Python
  script.
- The LLM follows the document and may call the script:
  - Script — deterministic file and database work
  - LLM — interpretation and judgment
- A stage uses one or more skills; a skill serves one or more stages.
- Each skill document opens with the assets it reads and writes.

### 5.2 — The skills

**T5.1 — The skills**

| # | Skill | Function | In | Out |
|---|---|---|---|---|
| 1 | `init-pipeline` | Create the blank framework and the KiCad project | T2.3, T2.4, T2.13 | `board.db`<br>`*.kicad_pro`, `*.kicad_sch`, `*.kicad_pcb`, `sym-lib-table`, `fp-lib-table`, `lib/` |
| 2 | `table-read` | Show the record, one view per stage | `board.db` | Markdown on stdout |
| 3 | `lib-index` | Index the KiCad symbol libraries | The installed `.kicad_sym` files | `lib/kicad-lib-index.json` |
| 4 | `copy-kicad-part` | Find a symbol, or a footprint with its 3D model, for a part in the KiCad libraries | the part file, KiCad symbol and footprint libraries | `<library>:<symbol>` or `<library>:<footprint>`, or `null`<br>part file — `symbol_donor`, `footprint_donor` |
| 5 | `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| 8 | `table-write` | Create or modify part; record a vendor price survey | Record row, vendor quote | `board.db` — `parts_table`, `ref_table`, `price_table` |
| 9 | `kicad-update` | Place instances; push record to library fields; pull library fields to record | `board.db`, `lib/`, `*.kicad_sch` | `*.kicad_sch`, `*.kicad_pcb`, `lib/*.kicad_sym`<br>`board.db` — `ref_table`, `parts_table` |

### 5.3 — Layout

- The tool is a Claude Code plugin; `tools/board-build/` is the plugin root
- `.claude-plugin/plugin.json` — the manifest
- `board-build-tool.md` — the only document at the top level
- `skills/<skill>/` — one folder per skill: `SKILL.md` and its script
- A script finds a sibling by `<root>/skills/<name>/<name>.py`; a document names its script by `${CLAUDE_PLUGIN_ROOT}/skills/<name>/<name>.py`
