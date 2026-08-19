#!/usr/bin/env python3
"""copy-kicad-part - find a symbol for a part in the KiCad libraries.

    copy-kicad-part.py <board-dir> <ipn> [--lib DIR ...]

It searches the libraries for a symbol that is the part, and prints
`<library>:<symbol>` when it finds one and `null` when it does not. It
copies nothing and it writes nothing; `symbol-draw --from` does the copying.

Null is an answer, not a failure. A part no library holds is a part that has
to be drawn, and the run exits zero saying so.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Where KiCad keeps its own symbol libraries. KICAD_SYMBOL_DIR overrides.
STOCK_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/share/kicad/symbols",
]

IPN = re.compile(r"^[A-Z]\d{4}$")
SYMBOL = re.compile(r'^\t\(symbol "([^"]+)"', re.M)


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"copy-kicad-part: {message}")


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
    """Every approved part number for the IPN, the one designed against
    first. Any of them may be the symbol's name - the alternatives of T1.3
    take the board as designed, so a symbol for one is a symbol for all."""
    if con.execute("select 1 from parts_table where ipn = ?",
                   (ipn,)).fetchone() is None:
        raise Bad(f"{ipn} is not in parts_table")
    rows = con.execute("select mpn from aml_table where ipn = ? "
                       "order by rank is not null, rank", (ipn,)).fetchall()
    if not rows:
        raise Bad(f"{ipn} has no row in aml_table. Name a part number first")
    return [r[0] for r in rows]


def stock_folder():
    import os
    for candidate in [os.environ.get("KICAD_SYMBOL_DIR")] + STOCK_CANDIDATES:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    raise Bad("KiCad symbol directory not found. Set KICAD_SYMBOL_DIR")


def libraries(extra):
    folders = [stock_folder()]
    for name in extra or []:
        folder = Path(name)
        if not folder.is_dir():
            raise Bad(f"{folder} is not a directory")
        folders.append(folder)
    out = []
    for folder in folders:
        out.extend(sorted(folder.glob("*.kicad_sym")))
    return out


def flat(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def core(mpn):
    """The part number without its ordering suffix. `USB3343-CP-TR` is
    ordered three ways and is one part; the library carries it once."""
    return flat(re.split(r"[-/]", mpn)[0])


def wildcard(name, key):
    """KiCad names a family with a trailing `x` where the letters that
    follow choose a package or a temperature grade - `STM32H735VGTx` is the
    part `STM32H735VGT6` is one of. The `x` stands for what the part number
    completes, and nothing else counts as a match."""
    if not name.endswith("x"):
        return False
    stem = name[:-1]
    return bool(stem) and key.startswith(stem) and len(key) - len(stem) <= 3


def search(paths, mpns):
    """The best match, or None. An exact name first, then the part number
    without its ordering suffix, then a KiCad family name the part number
    completes. Nothing looser: a symbol that is merely the same kind of part
    is not this part, and a wrong symbol on a sheet costs more than drawing
    one."""
    exact, near = [], []
    keys = [flat(m) for m in mpns]
    cores = [core(m) for m in mpns]
    for path in paths:
        try:
            src = path.read_text(errors="replace")
        except OSError:
            continue
        for name in SYMBOL.findall(src):
            flat_name = flat(name)
            if flat_name in keys:
                exact.append(f"{path.stem}:{name}")
            elif flat_name in cores or any(wildcard(flat_name, k)
                                           for k in keys + cores):
                near.append(f"{path.stem}:{name}")
    if exact:
        return sorted(exact)[0]
    if near:
        return sorted(near)[0]
    return None


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    args = ap.parse_args(argv[1:])

    if not Path(args.board).is_dir():
        raise Bad(f"{args.board} is not a directory")
    if not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")

    con = connect(args.board)
    try:
        mpns = part_numbers(con, args.ipn)
    finally:
        con.close()

    found = search(libraries(args.lib), mpns)
    print(found if found else "null")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
