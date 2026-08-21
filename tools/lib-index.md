# lib-index

Index the KiCad symbol libraries. No model runs here; it is parsing.

| Reads | Writes |
|---|---|
| the User's `.kicad_sym` files | `<board-dir>/lib/kicad-lib-index.json` |

```
python3 tools/board-build/tools/lib-index.py <board-dir> [--lib DIR ...] [--force]
```

One row per symbol: the library, the symbol, its reference prefix, its pin
count, its pins as number, name and type, and the description and keywords
the library carries.

## Where it lives

With the libraries it indexes — `lib/`, beside `sym-lib-table`, which is
what points at them. Not with the tool: the tool holds no state, and the
index is derived from the User's installation, not from anything the tool
knows.

## When it runs

`copy-kicad-part` builds it when it is missing or older than any library
file, so it need not be run by hand. `--force` rebuilds a current one.

## What it refuses

- A project folder that does not exist
- A `--lib` that is not a directory
- No KiCad symbol directory found

Each exits non-zero.
