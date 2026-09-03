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

- The LLM performs the stages using the nine skills — T5.1.

### 1.4 — The contract

- The structure — enforced and immutable:
  - The stages — T4.1
  - The nine skills — T5.1
  - The three tables with their keys — sections 2.3 and 2.9
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
| 1 | `board.db` | `project_table`, `parts_table`, `ref_table` |
| 2 | `lib/<project>.kicad_sym` | per IPN: `Value` — the MPN, else description — `Footprint`, `Description`, `Datasheet`, `Manufacturer`, `MPN`, `note`, `ipn` |
| 3 | `*.kicad_sch`, `*.kicad_pcb` | `Reference`, `ipn` |

- `board.db` is master. Part fields push to the library symbol; instance
  data pushes to the sheet; pull reads library fields back into the
  record.
- The schematic returns an instance the User placed on it — its existence
  and its `Reference`, nothing else. The tool deletes on neither side.
- Instances take library fields in KiCad — Update Symbols from Library.
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
| 1 | `ipn` | TEXT | Key | |
| 2 | `description` | TEXT | | Yes |
| 3 | `symbol` | TEXT | | Yes |
| 4 | `footprint` | TEXT | | Yes |
| 5 | `source` | TEXT | | Yes |
| 6 | `note` | TEXT | | Yes |
| 7 | `name` | TEXT | Unique | Yes |
| 8 | `mpn` | TEXT | | Yes |
| 9 | `manufacturer` | TEXT | | Yes |
| 10 | `datasheet` | TEXT | | Yes |

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

**T2.5 — `source` letters**

| # | Letter | Origin |
|---|---|---|
| 1 | `s` | KiCad stock libraries |
| 2 | `v` | Manufacturer or a publishing service |
| 3 | `h` | Drawn here, against the datasheet |
| 4 | `-` | Absent |

### 2.4 — The sourcing tables

- Removed, 2026-09-02 (n0.4). Sourcing lives on the part — `mpn`,
  `manufacturer`, `datasheet` in T2.3. This is a prototype's parts
  list, not an enterprise parts manager.

### 2.5 — The relations

**T2.9 — The relations**

| # | From | To | Cardinality | On delete |
|---|---|---|---|---|
| 1 | `ref_table.ipn` | `parts_table.ipn` | Many-to-one | Restrict |
| 2 | `ref_table.parent` | `ref_table.uuid` | Many-to-one, self | Set null |

- All are declared foreign keys.
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
| 2 | `Value` | the library symbol | `parts_table.mpn`, else `parts_table.description` |
| 3 | `Footprint` | the library symbol | `parts_table.footprint` |
| 4 | `Description` | the library symbol | `parts_table.description` |
| 5 | `Datasheet` | the library symbol | `parts_table.datasheet` |
| 6 | `Manufacturer` | the library symbol | `parts_table.manufacturer` |
| 7 | `MPN` | the library symbol | `parts_table.mpn` |
| 8 | `note` | the library symbol | `parts_table.note` |
| 9 | `ipn` | both | `parts_table.ipn`, the key |

- `Value` shows the part the board was designed against — what a person
  reads on a sheet. The IPN is the key and travels in its own field.

### 2.8 — Where a thing is placed

**T2.12 — Placement**

| # | Rank | Dimension | Schematic | Board |
|---|---|---|---|---|
| 1 | 1 | `page` | Selects the file | — |
| 2 | 2 | `room` | Block of the sheet | Region of the board |
| 3 | 3 | Family | Groups instances whose `room` is blank | Groups instances whose `room` is blank |

- A family whose instances carry two pages is placed on both.
- Order of rooms and families within a page is arbitrary — subject to
  re-entry, section 4.3.
- Order within a family is defined in the skill documents.

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
| 8 | `design/parts/` | part files - facts mined from the datasheet: `pins`; `package` at stage 5. `<IPN>-<name>.json` |
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
| 6 | `*.kicad_pro` | Generated once |
| 7 | `*.kicad_sch` | Updated by the tool, which does not wire |
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
| 3 | Update library — symbols | `copy-kicad-part`<br>`datasheet-read`<br>`symbol-draw` | — |
| 4 | Update schematic | `kicad-update` | Wires |
| 5 | Update library — footprints, 3D | `copy-kicad-part`<br>`datasheet-read`<br>`footprint-draw` | — |
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
| 1 | `board.db` and its three tables |
| 2 | `*.kicad_pro`, `*.kicad_sch`, `*.kicad_pcb`, `sym-lib-table`, `lib/<project>.kicad_sym` |

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
- Push rewrites library-symbol fields from the record; pull reads them
  back into it. Instance fields are not touched on re-entry — the User
  pulls them in KiCad, Update Symbols from Library.

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
| 1 | `init-pipeline` | Create the blank framework and the KiCad project | T2.3, T2.4, T2.13 | `board.db`<br>`*.kicad_pro`, `*.kicad_sch`, `*.kicad_pcb`, `sym-lib-table`, `lib/` |
| 2 | `table-read` | Show the record, one view per stage | `board.db` | Markdown on stdout |
| 3 | `lib-index` | Index the KiCad symbol libraries | The installed `.kicad_sym` files | `lib/kicad-lib-index.json` |
| 4 | `copy-kicad-part` | Find a symbol for a part in the KiCad libraries | `board.db`, KiCad libraries | `<library>:<symbol>`, or `null` |
| 5 | `datasheet-read` | Read a pinout and a package out of a datasheet | `datasheets/` | Pins, package, physical fields |
| 6 | `symbol-draw` | Copy or draw a symbol into `lib/`, fields written from the record | KiCad libraries, pins from `datasheet-read`, `board.db` | `lib/*.kicad_sym`<br>`parts_table` — `symbol`, `source` |
| 7 | `footprint-draw` | Copy or draw a footprint into `lib/` | KiCad libraries, package from `datasheet-read` | `lib/*.pretty`, `lib/3d/`<br>`parts_table` — `footprint`, `source` |
| 8 | `table-write` | Create or modify part | Record row | `board.db` — `parts_table`, `ref_table` |
| 9 | `kicad-update` | Place instances; push record to library fields; pull library fields to record | `board.db`, `lib/`, `*.kicad_sch` | `*.kicad_sch`, `*.kicad_pcb`, `lib/*.kicad_sym`<br>`board.db` — `ref_table`, `parts_table` |

### 5.3 — Layout

- `board-build-tool.md` — the only document at the top level
- `tools/` — one `<skill>.md` and its script, per skill
