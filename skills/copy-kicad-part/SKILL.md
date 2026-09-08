---
name: copy-kicad-part
description: Find and copy a KiCad symbol, or a footprint with its 3D model, into the project library. Stages 3 and 5.
---

# copy-kicad-part

Copy a KiCad symbol, or a footprint with its 3D model, into the project
library. Give it a part number or a description; it writes the copy and
prints what it wrote, or `null`.

| Reads | Writes |
|---|---|
| `lib/kicad-lib-index.json`<br>the KiCad libraries<br>`parts/<IPN>-<name>.json` | `lib/<nickname>.kicad_sym`<br>`lib/<nickname>.pretty/`<br>`lib/3d/`<br>`parts/<IPN>-<name>.json` — `symbol_donor`, `footprint_donor` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/copy-kicad-part/copy-kicad-part.py <board-dir> <hint> [--ipn IPN] [--nickname N] [--lib DIR ...]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/copy-kicad-part/copy-kicad-part.py <board-dir> --batch FILE [--lib DIR ...]
```

`--pins N` gives the part's pin count — a scoring preference.
`--pinout FILE` gives the pinout in a file — `[[number, name, type,
side], ...]` — the part file's `pins`, `datasheet-read.md` T1. Pins are
renumbered, renamed, added and deleted to match it. A batch entry may
carry `"pins"` or `"pinout"`. An exposed pad counts as a pin.

Every run ends with a KPI line: parts, tokens, tokens per part. Elapsed
is the caller's to measure.

`<hint>` is a part number, a description, or both. `--ipn` names the copy;
without it the copy is named after the hint.

It does not read `board.db` and does not write it. `symbol-draw` calls it,
and `symbol-draw` owns the record.

Drawing a symbol is the last resort. This is asked first.

## Show, choose, take

No second session runs here — the LLM performing the stage is the one
already running (section 1.3 of the tool document). Two calls:

| Call | Does | Model |
|---|---|---|
| without `--take` | builds or loads the index through `lib-index`, scores the hint, prints the shortlist — library, symbol, pin count, description, pins — and stops | no |
| with `--take LIBRARY:SYMBOL` | copies the named symbol, renames every pin by number from `--pinout`, runs the gates, prints the library id | no |

The judgment — which symbol — happens between the two calls, in the
running session, in the open. The gates hold either way: the pin count
must match, a named pin must exist, and the choice may be any symbol in
the libraries, not only the shortlist.

## What counts as the part

The graphic and the function decide. Take the symbol and apply the
pinout. The package
and the maker belong to the footprint, not the symbol, so a candidate in
another package is not a reason to refuse.

Null when nothing has pins that do the same job. Pin count alone is never
the test. A part that is not on a schematic at all — a bare board, an
enclosure, a host the board plugs into — is null.

`fit` comes back with the answer: `exact`, `family` or `generic`.

## What it writes

The symbol, under the name given, into `lib/<nickname>.kicad_sym`. Its
`Value` is that name, its `Footprint` is emptied — the package belongs to
`footprint-draw` — and an `origin` property names the library and symbol it
came from. On a take the part file `parts/<IPN>-<name>.json`, found by
the name, gains `symbol_donor` — or `footprint_donor` — naming the same.
No part file, nothing written there.

`init-pipeline` must have made the library first.

## Footprints

Package in, footprint out. No script runs here; the session performing
the stage does this, per part. For each part with a `package` in its part
file, look at the installed `.pretty` libraries — the index's footprint
rows — and choose the footprint that is that package: same body, same pad
count and pitch, whatever the name. Copy it into
`lib/<nickname>.pretty/<name>.kicad_mod`, renamed inside. Copy its 3D
model, when it names one, into `lib/3d/`, the path rewritten to
`${KIPRJMOD}/lib/3d/`. Nothing else in the footprint changes. Write
`footprint_donor` into the part file. No footprint is that package: leave
the part, say so.

## What comes back is checked

## What it refuses

- A project folder that does not exist, or with no `lib/<nickname>.kicad_sym`
- No `.kicad_pro` and no `--nickname`

An answer that fails validation — a library not on disk, a symbol the
library does not hold, a rename or unused entry naming a pin the symbol
does not have — becomes a null carrying the rejection reason, so the
caller's next resort still runs. It is printed, never silently dropped.

Each exits non-zero and names what it found.
