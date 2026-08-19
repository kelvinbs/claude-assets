# copy-kicad-part

Match the board's parts to symbols in the KiCad libraries. One run for the
whole board.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`<br>`lib/kicad-index.json` | nothing |

```
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> --all [--lib DIR ...]
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> <ipn> [<ipn> ...]
```

`--all` is every part the record puts on a page and that has no symbol yet.

`symbol-draw` calls it once before it draws anything, and copies what comes
back. Drawing a symbol is the last resort; this is asked first.

## One run, not one per part

Asked part by part, the question costs a model run each and every run starts
cold. Asked once, the run sees the whole board: the families that repeat
across it, the parts that are the same silicon under two IPNs, the generic
that serves six passives. It answers them together and consistently.

That is what the index buys — not the same work done faster, but a question
that could not be asked before.

## The index does the finding, the model does the judging

`lib-index` parses every library on the machine into `lib/kicad-index.json`,
built automatically when it is missing or out of date. This tool scores that
index against each part's number and the words of its description, and takes
the best candidates.

The prompt carries every part and its candidates, each with its pins. The
model reads no files. It is given what there is and asked which of it is the
part.

## What counts as the part

A similar part's symbol is this part's symbol when its pins do the same job.
Take it, and rename the pins to the names the datasheet prints. The package
and the maker belong to the footprint, not the symbol, so a candidate in
another package is not a reason to refuse.

Null when nothing listed has pins that do the same job. Pin count alone is
never the test. A part that is not on a schematic at all — a bare board, an
enclosure, a host the board plugs into — is null.

`fit` comes back with each answer: `exact`, `family` or `generic`.

## What comes back is checked

Every part named in the run has an answer, every named symbol was one of
that part's candidates, and every renamed pin is a pin that symbol has. A
run that fails any of these stops and names the part.

## What it refuses

- An IPN that does not read as one, or names no row
- IPNs named together with `--all`
- A `--lib` that is not a directory
- A board directory with no `board.db`, or one missing a table

Each exits non-zero and names what it found.
