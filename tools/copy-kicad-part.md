# copy-kicad-part

Copy a KiCad symbol, or a footprint with its 3D model, into the project
library. Give it a part number or a description; it writes the copy and
prints what it wrote, or `null`.

| Reads | Writes |
|---|---|
| `lib/kicad-lib-index.json`<br>the KiCad libraries<br>`parts/<IPN>-<name>.json` | `lib/<nickname>.kicad_sym`<br>`lib/<nickname>.pretty/`<br>`lib/3d/`<br>`parts/<IPN>-<name>.json` — `symbol_donor`, `footprint_donor` |

```
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> <hint> [--ipn IPN] [--nickname N] [--lib DIR ...]
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> --batch FILE [--lib DIR ...]
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> --footprint <name> [--take LIBRARY:FOOTPRINT]
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

`--footprint <name>` runs show, choose, take on the footprint rows of the
index. The part file's `package` and its pin count score the shortlist —
library, footprint, pad count, description. No `package` in the part
file, null. `--take LIBRARY:FOOTPRINT` copies the `.kicad_mod` into
`lib/<nickname>.pretty/` under the part's name, copies its 3D model into
`lib/3d/`, rewrites the model path to `${KIPRJMOD}/lib/3d/<file>` (section
3.3), and prints `<nickname>:<name>`. Nothing in the footprint is
modified. A footprint that names no model is copied without one, and says
so.

## What comes back is checked

## What it refuses

- A project folder that does not exist, or with no `lib/<nickname>.kicad_sym`
- No `.kicad_pro` and no `--nickname`

An answer that fails validation — a library not on disk, a symbol the
library does not hold, a rename or unused entry naming a pin the symbol
does not have — becomes a null carrying the rejection reason, so the
caller's next resort still runs. It is printed, never silently dropped.

Each exits non-zero and names what it found.
