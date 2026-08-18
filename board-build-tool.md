# board-build

A container. It holds the tools and the documents that take a board from
parts to fabrication output. Nothing in it is specific to one product.

## Contents

1. [Scope](#1--scope)
2. [Rooms](#2--rooms)
3. [Resources](#3--resources)
4. [Steps](#4--steps)
5. [Rules](#5--rules)
6. [State](#6--state)

## 1 — Scope

**T1.1 — What it covers**

| | |
|---|---|
| Starts at | a part that has a datasheet and a record row |
| Ends at | gerbers, drill, centroid, BOM, ready for a fab |
| Holds | tools, their documents, the schema they read |
| Does not hold | any one product's parts, boards or outputs |

## 2 — Rooms

Two different things carry the word. They are named apart and never
abbreviated to `room` alone.

**T2.1 — Room, both senses**

| Term | Lives on | Holds | Set by |
|---|---|---|---|
| sheet room | the schematic | which page a part is drawn on, and where on that page | placement intent |
| board room | the layout | a rectangle on the board a part must sit inside | floorplan |

**T2.2 — Fields**

| Field | Sense |
|---|---|
| `sheet` | sheet room — page name |
| `sheet_x`, `sheet_y` | sheet room — position on that page |
| `board_room` | board room — named region |
| `board_x`, `board_y` | board room — position within it |

## 3 — Resources

**T3.1 — What is owned and what is made**

| Resource | Owner |
|---|---|
| `parts.db` — part, aml, sourcing | hand, hand, generated |
| `lib/*.kicad_sym`, `lib/*.pretty`, `lib/3d` | local only, copies never links |
| `datasheets/` | hand |
| `*.kicad_sch`, `*.kicad_pcb` | generated, then hand for layout |
| `out/` — bom, netlist, gerbers, centroid | generated |

**T3.2 — Fields the schematic carries**

| Field | Built-in | Holds |
|---|---|---|
| `Reference` | yes | U3, C12 |
| `Value` | yes | what it is on the page |
| `Footprint` | yes | the land pattern |
| `IPN` | no | the join key into the database |

Four. Everything else is joined from the database at output time.

**T3.3 — Database tables**

| Table | Row | Owner |
|---|---|---|
| `part` | one per IPN — function, requirement, package, pins, pitch, footprint, symbol, datasheet, pins_checked, origin | hand |
| `aml` | one per IPN and MPN — manufacturer, rank, approved, note | hand |
| `sourcing` | one per MPN — distributor, part number, price, stock, lifecycle, library tier, priced_on | generated |

## 4 — Steps

**T4.1 — The path, and what the agent does at each step**

| # | Step | Input | Output | Agent |
|---|---|---|---|---|
| 1 | part definition | datasheet, record row | `part` row, IPN | reads the datasheet, writes the row, invents no value |
| 2 | placement intent | database, block diagram | sheet room and board room per part | assigns from a stated rule |
| 3 | library genesis | datasheet | symbol, footprint, 3D in the local library | draws from the land pattern, reports a pin verdict |
| 4 | schematic render | database and intent | `.kicad_sch` | generates the whole file |
| 5 | connectivity | nets from the database | nets drawn | ERC clean is the gate |
| 6 | layout | netlist, board rooms | `.kicad_pcb` | places inside the room, routes or hands off |
| 7 | verification | board | DRC, stackup, impedance | reports, waives nothing |
| 8 | fab output | board | gerber, drill, centroid, BOM | generates, checks against the fab's rules |
| 9 | sourcing | BOM | order tables | joins the database to the distributor APIs |

## 5 — Rules

**T5.1 — Fixed**

| | |
|---|---|
| Libraries | no reference resolves outside the project. A stock part is copied in, then owned |
| Generated artefacts | disposable. Only the database, the libraries and the intent are owned |
| Steps | no step edits the output of an earlier one by hand |
| Rooms | data, not layout judgement |
| Market data | never typed. It is read from an API or it is absent |

## 6 — State

**T6.1 — Where the tool stands**

| | |
|---|---|
| Written | this document |
| Built | nothing. `builds/proto1/tools` is the working precedent and is not yet moved here |
