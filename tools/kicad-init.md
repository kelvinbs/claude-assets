# kicad-init

Create the KiCad project from nothing. The third step of initialisation.

| Reads | Writes |
|---|---|
| nothing | `<project>.kicad_pro`<br>`lib/<project>.kicad_sym`<br>`sym-lib-table` |

```
python3 tools/board-build/tools/kicad-init.py <board-dir> --project NAME
```

It runs on an empty board directory and leaves an empty project: a project
file KiCad opens, a symbol library with no symbols in it, and the table entry
that resolves that library. Process 2 then has somewhere to put a symbol.

`--project` names the files and the library nickname. It is required — a
project name guessed from a directory name is how two projects end up
sharing a nickname.

## What it satisfies

Section 2. The table sits in the project directory and is committed, its one
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
- A project name that is not letters, digits, hyphen or underscore
- An entry that already carries this nickname and points somewhere other than
  `${KIPRJMOD}/lib/<project>.kicad_sym`. Nothing is changed, and the run names
  what it found

Each exits non-zero.
