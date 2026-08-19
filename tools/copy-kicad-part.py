#!/usr/bin/env python3
"""copy-kicad-part - find a symbol for a part in the KiCad libraries.

    copy-kicad-part.py <board-dir> <ipn> [--lib DIR ...]

Prints `<library>:<symbol>`, with any pin renames under it, or `null`.

Drawing a symbol is the last resort. This is asked first: does a library
already hold this part, or something similar whose pins do the same job.

The libraries are indexed by `lib-index` first - 22k symbols, parsed, no
model. This tool picks the candidates out of that index and asks one
question about them. The model is given the candidates and their pins in the
prompt and reads nothing, so a part costs one short run rather than a
search.
"""

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

TIMEOUT = 300
LABEL = "asking about the candidates"
CANDIDATES = 40      # what fits in a prompt and still covers a family

IPN = re.compile(r"^([A-Z])\d{4}$")

PROMPT = """Which of these KiCad symbols is the symbol for {mpn}?

The part is {ipn}, {description}. Its reference designator is {prefix}.

These are the candidates, drawn from every library on this machine. Each
line is the library, the symbol, its pin count, its description, and its
pins as number:name.

{candidates}

Classify the part and judge them. A similar part's symbol is this part's
symbol when its pins do the same job - take it, and rename the pins to the
names this part's datasheet prints. The package and the maker belong to the
footprint, not the symbol.

Null only when nothing here has pins that do the same job. Pin count alone
is never the test. A wrong symbol passes every check downstream and is found
on the bench.

A part that is not on a schematic at all - a bare board, an enclosure, a
cable, a host the board plugs into - is null.

Write one of these to {out} and nothing else.

    {{
      "library": "the library, exactly as listed",
      "symbol": "the symbol, exactly as listed",
      "rename": {{"3": "VCC"}},
      "fit": "exact, family or generic",
      "why": "one line"
    }}

    {{"library": null, "why": "one line - the nearest candidate and why it is not this part"}}

`rename` carries only the pins whose names differ from the datasheet. Leave
it empty when nothing needs changing. It is not a way to reshape a symbol:
if the pins are not the part's pins, this is not the part.
"""


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"copy-kicad-part: {message}")


def heartbeat(label):
    """A run that prints nothing for minutes cannot be told from a hung one."""
    stop = threading.Event()

    def tick():
        start = time.monotonic()
        while not stop.wait(15):
            print(f"    {label}  {int(time.monotonic() - start)}s", flush=True)
    threading.Thread(target=tick, daemon=True).start()
    return stop


def sibling(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repo_root(board):
    for folder in [Path(board).resolve()] + list(Path(board).resolve().parents):
        if (folder / ".git").exists():
            return folder
    return Path(board).resolve()


# ------------------------------------------------------------------ the record

def connect(board):
    path = Path(board) / "board.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table", "aml_table"} <= have:
        raise Bad(f"{path} is missing a table. Run db-init")
    return con


def part_numbers(con, ipn):
    if con.execute("select 1 from parts_table where ipn = ?",
                   (ipn,)).fetchone() is None:
        raise Bad(f"{ipn} is not in parts_table")
    # A part number is not required. A resistor is drawn as a resistor before
    # anybody decides which one to buy.
    return [r[0] for r in con.execute(
        "select mpn from aml_table where ipn = ? order by rank is not null, "
        "rank", (ipn,))]


# -------------------------------------------------------------- the candidates

WORD = re.compile(r"[a-z0-9]+")
NOISE = {"the", "and", "for", "one", "per", "with", "from", "its", "into",
         "board", "side", "each", "own", "carries", "shared", "same", "this"}


def words(text):
    return {w for w in WORD.findall((text or "").lower())
            if len(w) > 2 and w not in NOISE}


def score(row, keys, cores, terms, prefix):
    """How likely this symbol is the part. The part number first, then the
    words of the description, then the class the reference prefix names."""
    name = row["symbol"].lower()
    flat = re.sub(r"[^a-z0-9]", "", name)
    points = 0
    for key in keys:
        if flat == key:
            points += 100
        elif key and (flat.startswith(key[:6]) or key.startswith(flat[:6])):
            points += 40
    for c in cores:
        if c and (flat.startswith(c) or c.startswith(flat)):
            points += 30
    text = words(row["description"]) | words(row["keywords"]) | words(name)
    points += 3 * len(terms & text)
    if row["prefix"] == prefix:
        points += 2
    return points


def candidates(rows, mpns, description, prefix):
    keys = [re.sub(r"[^a-z0-9]", "", m.lower()) for m in mpns]
    cores = [re.sub(r"[^a-z0-9]", "", re.split(r"[-/]", m)[0].lower())
             for m in mpns]
    terms = words(description) | {w for m in mpns for w in words(m)}
    scored = [(score(r, keys, cores, terms, prefix), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["library"], sr[1]["symbol"]))
    return [r for _, r in scored[:CANDIDATES]]


def as_lines(rows):
    out = []
    for r in rows:
        pins = " ".join(f"{p['number']}:{p['name'] or '~'}" for p in r["pins"])
        out.append(f"    {r['library']}:{r['symbol']}  {r['pin_count']} pins"
                   f"  {r['description']}  [{pins}]")
    return "\n".join(out)


# ------------------------------------------------------------------- the asking

def faults(spec, rows):
    if spec.get("library") in (None, ""):
        return []
    name, symbol = spec.get("library"), spec.get("symbol")
    if not isinstance(name, str) or not isinstance(symbol, str) or not symbol:
        return ["library named without a symbol"]
    match = [r for r in rows if r["library"] == name and r["symbol"] == symbol]
    if not match:
        return [f"{name}:{symbol} was not one of the candidates"]
    numbers = {p["number"] for p in match[0]["pins"]}
    bad = []
    for key, value in (spec.get("rename") or {}).items():
        if str(key) not in numbers:
            bad.append(f"rename names pin {key}, which {symbol} does not have")
        if not str(value).strip():
            bad.append(f"rename gives pin {key} no name")
    return bad


def ask(ipn, description, mpn, prefix, rows, root):
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT.format(ipn=ipn, description=description or "no description",
                           mpn=mpn or "not named yet", prefix=prefix,
                           candidates=as_lines(rows), out=out)
    beat = heartbeat(f"{ipn} " + LABEL)
    try:
        run = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Write"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
            raise Bad(f"{ipn}: nothing written. " + " / ".join(tail))
        try:
            spec = json.load(open(out))
        except json.JSONDecodeError as exc:
            raise Bad(f"{ipn}: not JSON - {exc}")
    finally:
        beat.set()
        if out.exists():
            out.unlink()

    bad = faults(spec, rows)
    if bad:
        raise Bad(f"{ipn}: " + "; ".join(bad))
    return spec


# ------------------------------------------------------------------------ run

def find(con, board, ipn, extra=None):
    """The symbol for a part, with the renames it needs, or None. This is
    what `symbol-draw` calls."""
    row = con.execute("select description from parts_table where ipn = ?",
                      (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    mpns = part_numbers(con, ipn)
    classes = sibling("table-write").CLASSES
    prefix = classes[IPN.match(ipn).group(1)][1]

    rows = sibling("lib-index").build(board, extra)
    shortlist = candidates(rows, mpns, row[0], prefix)
    if not shortlist:
        return None
    spec = ask(ipn, row[0], mpns[0] if mpns else "", prefix, shortlist,
               repo_root(board))
    return spec if spec.get("library") else None


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")

    con = connect(board)
    try:
        spec = find(con, board, args.ipn, args.lib)
    finally:
        con.close()

    if spec is None:
        print("null")
        return 0
    print(f"{spec['library']}:{spec['symbol']}  ({spec.get('fit', '')})")
    for key, value in sorted((spec.get("rename") or {}).items(),
                             key=lambda kv: str(kv[0])):
        print(f"    rename pin {key} to {value}")
    print(f"    {spec.get('why', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
