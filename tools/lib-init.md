# lib-init

Create the symbol library the board project owns. Run once on a board
project, before `symbol-draw` first writes to it. It is outside the chain,
like `db-init`, and `symbol-draw` calls it on every run so the library is
never missing.

| Reads | Writes |
|---|---|
| `<board-dir>/*.kicad_pro` — for the nickname | `lib/<nickname>.kicad_sym`<br>`sym-lib-table` |

```
python3 tools/board-build/tools/lib-init.py <board-dir> [--nickname NAME]
```

## The nickname

Section 2 of `board-build-tool.md` requires the nickname to be the project's,
so a global entry on another machine cannot collide with it. The tool takes
it from the `.kicad_pro` in the board directory. With none there, or more
than one, it stops and asks for `--nickname` rather than guessing it from a
directory name — two projects sharing one nickname is exactly the collision
the rule exists to prevent.

## What it writes

An empty `kicad_symbol_lib` at `lib/<nickname>.kicad_sym`, and the entry in
`sym-lib-table` that resolves it:

```
(lib (name "<nickname>")(type "KiCad")(uri "${KIPRJMOD}/lib/<nickname>.kicad_sym")(options "")(descr "Symbols owned by this project"))
```

`${KIPRJMOD}` is the only path variable, per section 2, so a fresh clone
resolves it with nothing installed.

The table is edited in place, not rewritten. It is shared with every other
library the project points at, and those entries are not this tool's.

## Re-running

Safe, and `symbol-draw` relies on it being safe. A library that is there is
left exactly as it is, symbols included. An entry that is there is left
alone.

## What it refuses

- A board directory that does not exist
- No `.kicad_pro` and no `--nickname`, or more than one `.kicad_pro` and no
  `--nickname`
- A nickname that is not letters, digits, hyphen or underscore
- An entry that already carries this nickname and points somewhere other
  than `${KIPRJMOD}/lib/<nickname>.kicad_sym`. Nothing is changed, and the
  run names what it found. A nickname pointing at two libraries is a
  decision, not a merge

Each exits non-zero.
