# kicad-init

Create the KiCad project from nothing. Step 3 of the Init stage — T4.2 of
`board-build-tool.md`.

| Reads | Writes |
|---|---|
| `board.db` — presence only; the record precedes the project | `<project>.kicad_pro`<br>`<project>.kicad_sch`<br>`lib/<project>.kicad_sym`<br>`sym-lib-table` |

```
python3 tools/board-build/tools/kicad-init.py <board-dir>
```

It runs on a board directory holding `board.db` and leaves an empty
project: a project file KiCad opens, an empty root sheet, a symbol library
with no symbols in it, and the table entry that resolves that library.
Update library — symbols then has somewhere to put a symbol.

Project filenames take the board folder name — the folder names the files
and the library nickname.

## What it satisfies

Section 3.2. The table sits in the project directory and is committed, its one
path is `${KIPRJMOD}/lib/...`, and the nickname is the project's, so a global
entry on another machine cannot collide with it. A fresh clone opens with
nothing missing.

The project file is KiCad's own empty-project template, so KiCad opens it
without complaint.

## Re-running

Safe. A file that is there is left exactly as it is, symbols included. An
entry that is there is left alone.

## What it refuses

- A board directory that does not exist
- A board directory without `board.db` — init-pipeline runs first
- A project name that is not letters, digits, hyphen or underscore
- An entry that already carries this nickname and points somewhere other than
  `${KIPRJMOD}/lib/<project>.kicad_sym`. Nothing is changed, and the run names
  what it found

Each exits non-zero.
