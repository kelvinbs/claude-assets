# init-pipeline

Create the blank framework and the KiCad project — T4.2 of
`board-build-tool.md`. Run on an empty project folder, before anything
else.

| Reads | Writes |
|---|---|
| `board-build-tool.md` — T2.3, T2.4, T2.13 | `board.db` — `project_table`, `parts_table`, `ref_table`<br>`<project>.kicad_pro`<br>`<project>.kicad_sch`<br>`<project>.kicad_pcb`<br>`lib/<project>.kicad_sym`<br>`sym-lib-table` |

```
python3 tools/board-build/tools/init-pipeline.py <board-dir> [--scorch]
```

`--scorch` empties the project folder before the database is made. Every
file in it is made by the pipeline, so a full init deletes all of them and
starts from nothing. `.gitkeep` is left, so the directory survives a clone.
Without the flag nothing is removed.

`<board-dir>` is the design folder — `<project>/design/` (T3.1). It must
be named `design`; anything else is refused. `board.db` is written there,
beside the design files.

## The schema

Three tables in one file, and their columns are T2.3, T2.4 and
T2.13 of
`board-build-tool.md`.
`tools/init-pipeline.py` holds them in executable form and is the only
place they are written as DDL. A column added to those tables is added
there in the same commit.

Keys carry the constraint the document states: `ipn` on `parts_table`,
`uuid` on `ref_table`.

Every relation of T2.9 is declared — there are five and none is exempt.
`parent` sits on `ref_table`: parenthood is a property of use.
Tables are created in an order that lets a reference resolve, and
`parts_table` carries the unique index over `name`.

Everything else is nullable. A part is defined over several passes and a
row that is not finished is still a row; the skill that writes a field is
the skill that decides when it is required.

## What it does

- A database file that is absent is created.
- A table that is absent is created.
- A table that is there is left exactly as it is, rows included.

## What it refuses

A table whose columns are not the schema stops the run, and the run names
the file, the table, and the difference — a column missing, a column not in
the schema, or the order differing. Nothing is dropped, altered or emptied,
so the fix is a decision made in the open rather than a migration made
silently.

The run exits non-zero on that, and on a `<board-dir>` that is not a
directory.

## Re-running

Safe. It is the re-entry rule of section 4.3 applied to the schema: it adds
what is missing and leaves what is there. Re-run it after the schema gains
a table — the new one is created and the existing ones are untouched.

A column added to an existing table is not a case it handles. That is a
migration, and it is hand work.

## The KiCad project

The project root names the project: at first init the parent of `design/`
gives the name, stored in `project_table`, master thereafter — the tools
read it, never derive it again. The name changes only by editing the
table. It writes the project file,
the root sheet, the empty board, the empty symbol library, and the `sym-lib-table` entry
that resolves it — section 3.2 satisfied: the table sits in the project
directory, its one path is `${KIPRJMOD}/lib/...`, and the nickname is the
project's, so a fresh clone opens with nothing missing.

A nickname already present and pointing elsewhere stops the run and is
named. A file that is there is left exactly as it is, symbols included.
