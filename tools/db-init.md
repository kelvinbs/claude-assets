# db-init

Create the database and its tables. Run once on an empty board project,
before any process. It is outside the chain — see T3.1.

| Reads | Writes |
|---|---|
| `board-build-tool.md` — T1.2, T1.3 | `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table`, `offer_table` |

```
python3 tools/board-build/tools/db-init.py <board-dir>
```

`<board-dir>` is the KiCad project directory. `board.db` is written there,
beside the design files.

## The schema

Five tables in one file, and their columns are T1.2 and T1.3 of
`board-build-tool.md`.
`tools/db-init.py` holds them in executable form and is the only place
they are written as DDL. A column added to T1.2 or T1.3 is added there in
the same commit.

Keys carry the constraint the document states: `ipn` on `parts_table`,
`uuid` on `ref_table`, `ipn` + `mpn` on `aml_table`, `mpn` on `mpn_table`,
`mpn` + `distributor` + `break_qty` on `offer_table`.

The four declared relations of T1.5 are declared — `ref_table.ipn`,
`aml_table.ipn` and `parts_table.parent` against `parts_table.ipn`, and
`offer_table.mpn` against `mpn_table.mpn`. Tables are created in an order
that lets a reference resolve, and `aml_table` carries the unique index that
holds one blank rank per IPN.

Everything else is nullable. A part is defined over several passes and a row
that is not finished is still a row; the tool that writes a field is the tool
that decides when it is required.

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

Safe. It is the re-entry rule of T3.1 applied to the schema: it adds what is
missing and leaves what is there. Re-run it after T1.2 or T1.3 gains a
table — the new one is created and the existing ones are untouched.

A column added to an existing table is not a case it handles. That is a
migration, and it is hand work.
