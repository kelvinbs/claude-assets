# init-pipeline

Create the blank framework. Step 1 of the Init stage — T4.2 of
`board-build-tool.md`. Run on an empty board directory, before anything
else.

| Reads | Writes |
|---|---|
| `board-build-tool.md` — T2.3, T2.4, T2.6, T2.7, T2.8 | `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `offer_table` |

```
python3 tools/board-build/tools/init-pipeline.py <board-dir> [--scorch]
```

`--scorch` empties the board directory before the database is made. Every
file in it is made by the pipeline, so a full init deletes all of them and
starts from nothing. `.gitkeep` is left, so the directory survives a clone.
Without the flag nothing is removed.

`<board-dir>` is the KiCad project directory. `board.db` is written there,
beside the design files.

## The schema

Five tables in one file, and their columns are T2.3 through T2.8 of
`board-build-tool.md`.
`tools/init-pipeline.py` holds them in executable form and is the only
place they are written as DDL. A column added to those tables is added
there in the same commit.

Keys carry the constraint the document states: `ipn` on `parts_table`,
`uuid` on `ref_table`, `ipn` + `mpn` on `aml_table`, `mpn` on `mpn_table`,
`mpn` + `distributor` + `break_qty` on `offer_table`.

Every relation of T2.9 is declared — there are five and none is exempt.
Tables are created in an order that lets a reference resolve, and
`aml_table` carries the unique index that holds one blank rank per IPN.

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
