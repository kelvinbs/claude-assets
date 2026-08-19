#!/usr/bin/env python3
"""lib-index - index the KiCad symbol libraries.

    lib-index.py <board-dir> [--lib DIR ...] [--force]

One row per symbol in every library on the machine: the library, the symbol,
its reference prefix, its pin count, and its pin numbers, names and types,
with the description and the keywords the library carries. No model runs
here; it is parsing.

The index is derived from the User's libraries, so it is written where those
libraries are - `lib/kicad-index.json` in the board project, beside
`sym-lib-table`, which is what points at them. The tool holds no state of its
own.

`copy-kicad-part` builds it when it is missing or older than a library file,
so it need not be run by hand.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

INDEX = "kicad-index.json"

STOCK_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/share/kicad/symbols",
]

SYMBOL = re.compile(r'^\t\(symbol "([^"]+)"', re.M)
PROPERTY = re.compile(r'\(property "([^"]+)" "([^"]*)"')
PIN = re.compile(r'\(pin (\w+) \w+[^(]*(?:\([^()]*\)[^(]*)*?'
                 r'\(name "([^"]*)"[^)]*\)[^(]*(?:\([^()]*\)[^(]*)*?'
                 r'\(number "([^"]*)"', re.S)


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"lib-index: {message}")


def stock_folder():
    for candidate in [os.environ.get("KICAD_SYMBOL_DIR")] + STOCK_CANDIDATES:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    raise Bad("KiCad symbol directory not found. Set KICAD_SYMBOL_DIR")


def folders(extra):
    out = [stock_folder()]
    for name in extra or []:
        folder = Path(name)
        if not folder.is_dir():
            raise Bad(f"{folder} is not a directory")
        out.append(folder)
    return out


def blocks(src):
    """Each top-level symbol, as (name, text). A derived symbol carries only
    what it overrides, and that is what the index records - its parent is in
    the index too."""
    starts = [(m.start(), m.group(1)) for m in SYMBOL.finditer(src)]
    for i, (at, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        yield name, src[at:end]


def read_library(path):
    src = path.read_text(errors="replace")
    rows = []
    for name, block in blocks(src):
        props = dict(PROPERTY.findall(block))
        pins = [{"number": number, "name": pname, "type": ptype}
                for ptype, pname, number in PIN.findall(block)]
        rows.append({
            "library": path.stem,
            "symbol": name,
            "prefix": props.get("Reference", ""),
            "description": props.get("Description", ""),
            "keywords": props.get("ki_keywords", ""),
            "extends": (re.search(r'\(extends "([^"]+)"\)', block) or
                        [None, ""])[1] if "(extends " in block else "",
            "pin_count": len(pins),
            "pins": pins,
        })
    return rows


def index_path(board):
    return Path(board) / "lib" / INDEX


def stale(path, paths):
    """Older than any library it indexes, or absent."""
    if not path.exists():
        return True
    when = path.stat().st_mtime
    return any(p.stat().st_mtime > when for p in paths)


def library_files(extra):
    out = []
    for folder in folders(extra):
        out.extend(sorted(folder.glob("*.kicad_sym")))
    return out


def build(board, extra=None, force=False):
    """The index, built if it is missing or out of date. Returns its rows."""
    paths = library_files(extra)
    if not paths:
        raise Bad("no .kicad_sym files found")
    out = index_path(board)
    if not force and not stale(out, paths):
        return json.load(open(out))["symbols"]

    rows = []
    for path in paths:
        rows.extend(read_library(path))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({"built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "libraries": [str(p) for p in paths],
                   "symbols": rows}, f)
    return rows


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    ap.add_argument("--force", action="store_true",
                    help="rebuild an index that is already current")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")

    started = time.monotonic()
    rows = build(board, args.lib, args.force)
    libraries = len({r["library"] for r in rows})
    print(f"{index_path(board)}  {len(rows)} symbols, {libraries} libraries, "
          f"{time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
