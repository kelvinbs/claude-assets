# copy-kicad-part

Copy a KiCad symbol into the project library. Give it a part number or a
description; it writes the symbol and prints what it wrote, or `null`.

| Reads | Writes |
|---|---|
| `lib/kicad-lib-index.json`<br>the KiCad libraries | `lib/<nickname>.kicad_sym` |

```
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> <hint> [--ipn IPN] [--nickname N] [--lib DIR ...]
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> --batch FILE [--lib DIR ...]
```

`<hint>` is a part number, a description, or both. `--ipn` names the copy;
without it the copy is named after the hint.

It does not read `board.db` and does not write it. `symbol-draw` calls it,
and `symbol-draw` owns the record.

Drawing a symbol is the last resort. This is asked first.

## Six steps

| # | Step | Model |
|---|---|---|
| 1 | Take the hint | no |
| 2 | Build or load the index, through `lib-index` | no |
| 3 | Emit search queries per part — synonyms, family, class fallbacks, generic names | yes |
| 4 | Run every query over the index; union the hits with the base shortlist, per-query cap, union cap | no |
| 5 | Ask once which candidate is the part, and what its pins should be called | yes |
| 6 | Copy it into `lib/`, rename the pins, set `origin`, print the library id | no |

Two model calls per run — steps 3 and 5 — and both read nothing: hints and
candidates are in the prompt. Step 3 keeps recall off the hint's literal
tokens, so a generic symbol (`Device:R`, `Device:Antenna`) stays reachable;
step 4 is deterministic; step 5 can only pick from what step 4 fed it. If
step 3 fails the run falls back to the base shortlist alone.

## What counts as the part

A similar part's symbol is this part's symbol when its pins do the same job.
Take it, and rename the pins to the names the datasheet prints. The package
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
came from.

`init-pipeline` must have made the library first.

## What comes back is checked

The named symbol was one of the candidates, and every renamed pin is a pin
it has. A run that fails either stops and says so.

## What it refuses

- A project folder that does not exist, or with no `lib/<nickname>.kicad_sym`
- No `.kicad_pro` and no `--nickname`
- A library the answer names that is not on disk
- A symbol the library does not hold
- An answer naming a symbol that was not a candidate, or a pin the symbol
  does not have

Each exits non-zero and names what it found.
