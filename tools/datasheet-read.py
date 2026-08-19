#!/usr/bin/env python3
"""datasheet-read — read a pinout out of a datasheet.

    datasheet-read.py <board-dir> <ipn> [--datasheet PATH] [--force]
    datasheet-read.py <board-dir> --all [--force]

The first tool of process 2. It reads the part out of `board.db`, finds its
datasheet, and writes what the datasheet says to `parts/<IPN>.json`, which
is what `symbol-draw` draws from.

There is no parser here. Datasheets do not share a layout — a table, a
package drawing, a column beside prose, a scan with no text in it — so a
parser is a new special case for every part and never converges. Reading a
pinout is looking at the page, which is what `claude -p` is handed the job
of doing. What comes back is checked here, and a file that does not hold up
is deleted, so a bad pinout never reaches a symbol.

`parts/` is derived. Delete it and run this again.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

PIN_TYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector",
    "open_emitter", "no_connect",
}
SIDES = {"L", "R", "T", "B"}

IPN = re.compile(r"^[A-Z]\d{4}$")
TIMEOUT = 900

# The instruction the reader is given. It is the thing to change when a
# pinout comes out wrong — not the checks below, which only say whether what
# came back is a pinout at all.
PROMPT = """Read the pinout of {mpn} out of its datasheet.

The part is {ipn}, {description}. Its datasheet is {datasheet}, in this
repository.

The pinout will be a figure, a table, or both, and it will not be in the
same shape as the last one you read. Render pages and look at them:

    pdfinfo <pdf>
    pdftotext -layout <pdf> -            # if there is a text layer
    pdftoppm -png -r 150 -f N -l N <pdf> /tmp/pg     # then read /tmp/pg-N.png

Do not stop at the text layer. A scanned datasheet has none, and a pinout
drawn as a package outline is a picture whichever way the file was made.

If the datasheet in the repository does not carry the pinout, or carries it
for a different package than the part number names, go and find one that
does. Search the web, fetch the manufacturer's page. Do not give up and do
not guess a pin.

Write this to {out} and nothing else:

    {{
      "source": "the figure or table you read, by number and page",
      "pins": [[1, "GND", "power_in", "B"], [2, "RFIN", "input", "L"]]
    }}

    number   as printed on the package. Every pin, 1 to N, none missing
    name     as printed, exactly. Do not tidy it or expand it
    type     one of: input output bidirectional tri_state passive free
             unspecified power_in power_out open_collector open_emitter
             no_connect
    side     L R T B. Inputs left, outputs right, supplies top, grounds
             bottom, and use your judgement where that does not fit

An exposed pad is a pin. It numbers after the last numbered pin.

Where the part number names a package variant the datasheet tabulates
separately — a suffix that means MSOP against LFCSP, or one member of a
family — take that variant's pinout and say which in "source".

The IPN, the description and the path to the datasheet are the database's.
The tool fills them in around what you write.

Before you finish, read your file back against the figure pin by pin. A
wrong pin number passes ERC, passes the netlist, passes layout, and is
found on the bench with a board in your hand.
"""


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"datasheet-read: {message}")


# ------------------------------------------------------------------ the record

def connect(board):
    path = Path(board) / "board.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table", "aml_table", "mpn_table"} <= have:
        raise Bad(f"{path} is missing a table. Run db-init")
    return con


def designed_mpn(con, ipn):
    """The MPN the board was designed against — the blank-rank row of T1.3.
    A pinout is read for that one part, not for the alternatives, which take
    the board as designed and so take its symbol too."""
    rows = con.execute(
        "select a.mpn, m.datasheet from aml_table a "
        "left join mpn_table m on m.mpn = a.mpn "
        "where a.ipn = ? order by a.rank is not null, a.rank",
        (ipn,)).fetchall()
    if not rows:
        raise Bad(f"{ipn} has no row in aml_table. Name a part number first")
    return rows[0]


def part_row(con, ipn):
    row = con.execute(
        "select ipn, description from parts_table where ipn = ?",
        (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    return row


# -------------------------------------------------------------- the datasheet

def repo_root(board):
    for folder in [Path(board).resolve()] + list(Path(board).resolve().parents):
        if (folder / ".git").exists():
            return folder
    return Path(board).resolve()


def sheet_folder(board, given):
    """The project's own datasheets if it has them, otherwise the
    repository's. T2.1 owns the directory; where it sits is the project's."""
    if given:
        folder = Path(given)
        if not folder.is_dir():
            raise Bad(f"{folder} is not a directory")
        return folder
    here = Path(board) / "datasheets"
    if here.is_dir():
        return here
    there = repo_root(board) / "datasheets"
    if there.is_dir():
        return there
    raise Bad("no datasheets directory found. Give --datasheets")


def flat(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_sheet(folder, mpn):
    """A datasheet is matched to a part number by name. Two matches is not a
    match — the run says which files it saw and stops, because taking the
    first one is how a symbol gets drawn from the wrong part."""
    key = flat(mpn)
    hits = [p for p in sorted(folder.glob("*.pdf")) if key and key in flat(p.stem)]
    if not hits:
        hits = [p for p in sorted(folder.glob("*.pdf"))
                if flat(p.stem) and flat(p.stem) in key]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    raise Bad(f"{mpn} matches {len(hits)} files in {folder}: "
              + ", ".join(p.name for p in hits) + ". Give --datasheet")


def as_recorded(path, board):
    """Stored relative to the repository, so the record moves with a clone."""
    root = repo_root(board)
    path = Path(path).resolve()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def resolve_sheet(con, board, ipn, mpn, recorded, given, folder):
    """Given, then recorded, then found. Whatever it settles on is written
    back to mpn_table, so the second run needs no argument."""
    if given:
        path = Path(given)
        if not path.exists():
            raise Bad(f"{path} does not exist")
    elif recorded:
        path = repo_root(board) / recorded
        if not path.exists():
            path = Path(recorded)
        if not path.exists():
            raise Bad(f"{ipn}: mpn_table has {recorded} for {mpn}, "
                      f"and it is not on disk")
    else:
        found = find_sheet(sheet_folder(board, folder), mpn)
        if found is None:
            raise Bad(f"{ipn}: no datasheet found for {mpn}. "
                      f"Give --datasheet")
        path = found

    stored = as_recorded(path, board)
    if stored != recorded:
        con.execute("update mpn_table set datasheet = ? where mpn = ?",
                    (stored, mpn))
        con.commit()
    return path, stored


# ------------------------------------------------------------------ the reader

def faults(spec):
    """Everything wrong with a pinout, named. Empty means it holds up."""
    bad = []
    pins = spec.get("pins")
    if not isinstance(pins, list) or not pins:
        return ["no pins"]

    numbers = []
    for row in pins:
        if not isinstance(row, list) or len(row) != 4:
            bad.append(f"{row} is not [number, name, type, side]")
            continue
        number, name, etype, side = row
        if not isinstance(number, int):
            bad.append(f"pin {number!r} is not a number")
            continue
        numbers.append(number)
        if not str(name).strip():
            bad.append(f"pin {number} has no name")
        if etype not in PIN_TYPES:
            bad.append(f"pin {number} has type {etype!r}")
        if side not in SIDES:
            bad.append(f"pin {number} is on side {side!r}")

    if numbers:
        if len(set(numbers)) != len(numbers):
            seen = {n for n in numbers if numbers.count(n) > 1}
            bad.append(f"pins repeated: {sorted(seen)}")
        gaps = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        if gaps:
            bad.append(f"pins missing: {gaps}")
    return bad


def read(ipn, description, mpn, datasheet, out, root, quiet=False):
    """Hand the datasheet over and take back a pinout, or None."""
    if out.exists():
        out.unlink()
    prompt = PROMPT.format(ipn=ipn, description=description or "no description",
                           mpn=mpn, datasheet=datasheet, out=out)
    run = subprocess.run(
        ["claude", "-p", prompt,
         "--permission-mode", "acceptEdits",
         "--allowedTools", "Bash,Read,Write,WebSearch,WebFetch"],
        cwd=root, capture_output=True, text=True, timeout=TIMEOUT)

    if not out.exists():
        if not quiet:
            tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
            print(f"    {ipn}: nothing written. " + " / ".join(tail))
        return None

    try:
        spec = json.load(open(out))
    except json.JSONDecodeError as exc:
        out.unlink()
        if not quiet:
            print(f"    {ipn}: not JSON — {exc}")
        return None

    bad = faults(spec)
    if bad:
        out.unlink()
        if not quiet:
            for fault in bad:
                print(f"    {ipn}: {fault}")
        return None
    return spec


# ------------------------------------------------------------------------ run

def one(con, board, ipn, args):
    ipn, description = part_row(con, ipn)
    out = Path(board) / "parts" / f"{ipn}.json"
    if out.exists() and not args.force:
        print(f"{ipn}  already read — {out}")
        return True

    mpn, recorded = designed_mpn(con, ipn)
    path, stored = resolve_sheet(con, board, ipn, mpn, recorded,
                                 args.datasheet, args.datasheets)

    out.parent.mkdir(parents=True, exist_ok=True)
    spec = read(ipn, description, mpn, stored, out, repo_root(board))
    if spec is None:
        print(f"{ipn}  not read")
        return False

    # The identity is the database's. Only "source" and "pins" came from the
    # reader, and they are the only things it is allowed to have written.
    spec = {
        "ipn": ipn,
        "description": description,
        "mpn": mpn,
        "datasheet": stored,
        "source": spec.get("source", ""),
        "pins": sorted(spec["pins"], key=lambda row: row[0]),
    }
    with open(out, "w") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")
    print(f"{ipn}  {len(spec['pins'])} pins  {stored}")
    return True


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn", nargs="?")
    ap.add_argument("--all", action="store_true",
                    help="every part with no symbol yet")
    ap.add_argument("--datasheet", help="the PDF, when the name does not match")
    ap.add_argument("--datasheets", help="the directory to search")
    ap.add_argument("--force", action="store_true",
                    help="read again over a pinout already on disk")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if bool(args.ipn) == bool(args.all):
        raise Bad("name one IPN, or --all")
    if args.ipn and not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")
    if args.all and args.datasheet:
        raise Bad("--datasheet names one file, so it names one part")

    con = connect(board)
    try:
        if args.ipn:
            targets = [args.ipn]
        else:
            targets = [r[0] for r in con.execute(
                "select ipn from parts_table where symbol is null order by ipn")]
            if not targets:
                print("every part has a symbol. Nothing to read")
                return 0

        failed = []
        for ipn in targets:
            try:
                if not one(con, board, ipn, args):
                    failed.append(ipn)
            except Bad as exc:
                if len(targets) == 1:
                    raise
                print(str(exc))
                failed.append(ipn)
    finally:
        con.close()

    if failed:
        print(f"\n{len(failed)} of {len(targets)} not read: "
              + " ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
