#!/usr/bin/env python3
"""copy-kicad-part - match the board's parts to symbols in the KiCad libraries.

    copy-kicad-part.py <board-dir> [--all | <ipn> ...] [--lib DIR ...]

One run for the whole board. Every part is matched in the same run, against
candidates drawn from the `lib-index` index, and the answer per part is a
symbol with the renames it needs, or null.

Asked part by part, the same question costs a model run each time and each
run starts cold. Asked once, the run sees the whole board at once - the
families that repeat across it, the parts that are the same silicon under
two IPNs, the generic that serves six passives - and answers them together.
That is what the index buys: not the same work done faster, but a question
that could not be asked before.

Drawing a symbol is the last resort. This is asked first.
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

TIMEOUT = 1800
CANDIDATES = 18      # per part, and every part's list is in the one prompt
PINS_SHOWN = 20      # a 100-pin MCU does not need its pins listed to be found

IPN = re.compile(r"^([A-Z])\d{4}$")

PROMPT = """Match each part of this board to a symbol in the KiCad libraries.

Every part is below, with the candidates the index turned up for it. Each
candidate line is `library:symbol`, its pin count, its description, and its
pins as number:name.

{parts}

For each part, decide which candidate is its symbol.

A similar part's symbol is this part's symbol when its pins do the same job.
Take it, and rename the pins to the names this part's datasheet prints. The
package and the maker belong to the footprint, not the symbol - a candidate
in another package is not a reason to refuse.

Null when nothing listed has pins that do the same job. Pin count alone is
never the test. A part that is not on a schematic at all - a bare board, an
enclosure, a host the board plugs into - is null.

You are seeing the whole board at once. Use it: parts that are the same
silicon under two IPNs take the same symbol, a family that repeats should be
answered consistently, and a decision you make for one passive is the
decision for the rest of them.

Write this to {out} and nothing else - one object per part, every part
present:

    {{
      "A0001": {{"library": "RF_Amplifier", "symbol": "PGA-103+",
                 "rename": {{"3": "VDD"}}, "fit": "family",
                 "why": "one line"}},
      "A0002": {{"library": null, "why": "one line - the nearest candidate and why it is not this part"}}
    }}

`rename` carries only the pins whose names differ from the datasheet. Leave
it out when nothing needs changing. It is not a way to reshape a symbol: if
the pins are not the part's pins, the answer is null.

`fit` is `exact`, `family` or `generic`.
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
    if not {"parts_table", "ref_table", "aml_table"} <= have:
        raise Bad(f"{path} is missing a table. Run db-init")
    return con


def parts(con, ipns):
    """The parts to match, each with its description and its part number.
    With no IPN named, every part the record puts on a page and that has no
    symbol yet."""
    if ipns:
        rows = con.execute(
            "select ipn, description from parts_table where ipn in (%s) "
            "order by ipn" % ",".join("?" * len(ipns)), tuple(ipns)).fetchall()
        found = {r[0] for r in rows}
        missing = [i for i in ipns if i not in found]
        if missing:
            raise Bad("not in parts_table: " + " ".join(missing))
    else:
        rows = con.execute(
            "select p.ipn, p.description from parts_table p "
            "where p.symbol is null and exists (select 1 from ref_table r "
            "where r.ipn = p.ipn and trim(coalesce(r.page,'')) <> '') "
            "order by p.ipn").fetchall()
    out = []
    for ipn, description in rows:
        mpns = [r[0] for r in con.execute(
            "select mpn from aml_table where ipn = ? "
            "order by rank is not null, rank", (ipn,))]
        out.append({"ipn": ipn, "description": description or "",
                    "mpns": mpns})
    return out


# -------------------------------------------------------------- the candidates

WORD = re.compile(r"[a-z0-9]+")
NOISE = {"the", "and", "for", "one", "per", "with", "from", "its", "into",
         "board", "side", "each", "own", "carries", "shared", "same", "this"}


def words(text):
    return {w for w in WORD.findall((text or "").lower())
            if len(w) > 2 and w not in NOISE}


def score(row, keys, cores, terms, prefix):
    """The part number first, then the words of the description, then the
    class the reference prefix names."""
    flat = re.sub(r"[^a-z0-9]", "", row["symbol"].lower())
    points = 0
    for key in keys:
        if flat == key:
            points += 100
        elif key and (flat.startswith(key[:6]) or key.startswith(flat[:6])):
            points += 40
    for c in cores:
        if c and (flat.startswith(c) or c.startswith(flat)):
            points += 30
    text = words(row["description"]) | words(row["keywords"]) | words(flat)
    points += 3 * len(terms & text)
    if row["prefix"] == prefix:
        points += 2
    return points


def shortlist(rows, part, prefix):
    mpns = part["mpns"]
    keys = [re.sub(r"[^a-z0-9]", "", m.lower()) for m in mpns]
    cores = [re.sub(r"[^a-z0-9]", "", re.split(r"[-/]", m)[0].lower())
             for m in mpns]
    terms = words(part["description"]) | {w for m in mpns for w in words(m)}
    scored = [(score(r, keys, cores, terms, prefix), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["library"], sr[1]["symbol"]))
    return [r for _, r in scored[:CANDIDATES]]


def as_block(part, prefix, rows):
    mpn = part["mpns"][0] if part["mpns"] else "no part number yet"
    head = f"{part['ipn']}  {mpn}  ref {prefix}  {part['description']}"
    if not rows:
        return head + "\n    no candidates\n"
    lines = []
    for r in rows:
        pins = r["pins"][:PINS_SHOWN]
        text = " ".join(f"{p['number']}:{p['name'] or '~'}" for p in pins)
        if len(r["pins"]) > PINS_SHOWN:
            text += " ..."
        lines.append(f"    {r['library']}:{r['symbol']}  {r['pin_count']} pins"
                     f"  {r['description']}  [{text}]")
    return head + "\n" + "\n".join(lines) + "\n"


# ------------------------------------------------------------------- the asking

def faults(ipn, spec, rows):
    if not isinstance(spec, dict):
        return [f"{ipn}: the answer is not an object"]
    if spec.get("library") in (None, ""):
        return []
    name, symbol = spec.get("library"), spec.get("symbol")
    if not isinstance(name, str) or not isinstance(symbol, str) or not symbol:
        return [f"{ipn}: a library named without a symbol"]
    match = [r for r in rows if r["library"] == name and r["symbol"] == symbol]
    if not match:
        return [f"{ipn}: {name}:{symbol} was not one of its candidates"]
    numbers = {p["number"] for p in match[0]["pins"]}
    bad = []
    for key, value in (spec.get("rename") or {}).items():
        if str(key) not in numbers:
            bad.append(f"{ipn}: rename names pin {key}, not in {symbol}")
        if not str(value).strip():
            bad.append(f"{ipn}: rename gives pin {key} no name")
    return bad


def ask(blocks, lists, root):
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT.format(parts="\n".join(blocks), out=out)
    beat = heartbeat(f"matching {len(lists)} parts")
    try:
        run = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Write"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
            raise Bad("nothing written. " + " / ".join(tail))
        try:
            answer = json.load(open(out))
        except json.JSONDecodeError as exc:
            raise Bad(f"not JSON - {exc}")
    finally:
        beat.set()
        if out.exists():
            out.unlink()

    if not isinstance(answer, dict):
        raise Bad("the answer is not an object keyed by IPN")
    missing = [i for i in lists if i not in answer]
    if missing:
        raise Bad("no answer for " + " ".join(missing))
    bad = []
    for ipn, spec in answer.items():
        if ipn in lists:
            bad.extend(faults(ipn, spec, lists[ipn]))
    if bad:
        raise Bad("; ".join(bad))
    return answer


# ------------------------------------------------------------------------ run

def match(con, board, ipns=None, extra=None):
    """Every part matched in one run. Returns {ipn: spec or None}."""
    targets = parts(con, ipns)
    if not targets:
        return {}
    classes = sibling("table-write").CLASSES
    rows = sibling("lib-index").build(board, extra)

    blocks, lists = [], {}
    for part in targets:
        prefix = classes[IPN.match(part["ipn"]).group(1)][1]
        candidates = shortlist(rows, part, prefix)
        lists[part["ipn"]] = candidates
        blocks.append(as_block(part, prefix, candidates))

    answer = ask(blocks, lists, repo_root(board))
    return {ipn: (spec if spec.get("library") else None)
            for ipn, spec in answer.items() if ipn in lists}


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="every part on a page with no symbol yet")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if bool(args.ipn) == bool(args.all):
        raise Bad("name the IPNs, or --all")
    for ipn in args.ipn:
        if not IPN.match(ipn):
            raise Bad(f"'{ipn}' is not an IPN")

    con = connect(board)
    try:
        found = match(con, board, args.ipn or None, args.lib)
    finally:
        con.close()

    if not found:
        print("nothing to match")
        return 0
    hits = 0
    for ipn in sorted(found):
        spec = found[ipn]
        if spec is None:
            print(f"{ipn}  null")
            continue
        hits += 1
        renames = spec.get("rename") or {}
        tail = f"  {len(renames)} pin(s) renamed" if renames else ""
        print(f"{ipn}  {spec['library']}:{spec['symbol']}  "
              f"({spec.get('fit', '')}){tail}")
    print(f"\n{hits} of {len(found)} found in the KiCad libraries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
