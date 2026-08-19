#!/usr/bin/env python3
"""copy-kicad-part - find a symbol for a part in the KiCad libraries.

    copy-kicad-part.py <board-dir> <ipn> [--lib DIR ...]

Prints `<library>:<symbol>`, with any pin renames under it, or `null`.

Drawing a symbol is the last resort. This is what is asked first: does a
library already hold this part, or something close enough that renaming a
pin or two makes it this part. `null` is the answer that sends the part on
to be drawn.

There is no name matcher here. A part number is not what a library is
organised by - a symbol may carry the family name, the die name, a package
suffix the order code does not, or a generic name with the part number only
in its description. Matching strings finds the easy half and misses the
rest, so the search is handed to `claude -p`, which reads the libraries the
way a person would and says which symbol is the part.

What comes back is checked here against the library file: the symbol exists,
and every renamed pin is a pin it has.
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
from pathlib import Path

# Where KiCad keeps its own symbol libraries. KICAD_SYMBOL_DIR overrides.
STOCK_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/share/kicad/symbols",
]

TIMEOUT = 900

IPN = re.compile(r"^([A-Z])\d{4}$")


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


def repo_root(board):
    for folder in [Path(board).resolve()] + list(Path(board).resolve().parents):
        if (folder / ".git").exists():
            return folder
    return Path(board).resolve()


def sibling(name):
    """The class table is written once, in the tool that owns it."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).resolve().parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def folders(extra):
    folders = [stock_folder()]
    for name in extra or []:
        folder = Path(name)
        if not folder.is_dir():
            raise Bad(f"{folder} is not a directory")
        folders.append(folder)
    return folders


PROMPT = """Find the KiCad symbol for {mpn} in the libraries on this machine.

The part is {ipn}, {description}. Its reference designator is {prefix}.

The libraries are `.kicad_sym` files in these directories:

{folders}

Each is s-expression text. A symbol is a top-level `(symbol "NAME"` block
carrying `(pin <type> line ... (name "X") (number "N"))` for every pin, and
`(property "Description" "...")`. Search them however you like - grep for
the part number, for the family, for words from the description.

A library does not organise itself by order code. The symbol may carry the
family name with a trailing `x` for the package letters, the die name, a
package suffix the order code does not have, or a generic name with the part
number only in its description. Look past the file names.

Return one of two things, written to {out} and nothing else.

The part, when a symbol is it:

    {{
      "library": "the .kicad_sym file's name, without the extension",
      "symbol": "the symbol's name, exactly",
      "rename": {{"3": "VCC"}},
      "why": "one line - what makes this symbol this part"
    }}

`rename` is for a symbol that is the part but names a pin differently from
the datasheet. Key is the pin number as printed, value is the name it should
carry. Leave it empty when nothing needs changing. Do not use it to reshape
a symbol: if the pins are not the part's pins, this is not the part.

Nothing, when no symbol is it:

    {{"library": null, "why": "one line - what you searched and what was there"}}

Null is the right answer more often than not, and it is not a failure. A
symbol for a different member of a family, a part with the same pin count,
or the same kind of part from another maker is NOT this part. A wrong symbol
passes every check downstream and is found on the bench. When in doubt,
return null and let the part be drawn from its datasheet.
"""


def faults(spec, folders):
    """Everything wrong with an answer, named. Empty means it holds up."""
    if spec.get("library") in (None, ""):
        return []
    name, symbol = spec.get("library"), spec.get("symbol")
    if not isinstance(name, str) or not isinstance(symbol, str) or not symbol:
        return ["library named without a symbol"]

    path = None
    for folder in folders:
        candidate = folder / f"{name}.kicad_sym"
        if candidate.exists():
            path = candidate
            break
    if path is None:
        return [f"no library '{name}' in the directories searched"]

    src = path.read_text(errors="replace")
    if f'(symbol "{symbol}"' not in src:
        return [f"{path.name} does not hold a symbol '{symbol}'"]

    block = src[src.index(f'(symbol "{symbol}"'):]
    numbers = set(re.findall(r'\(number "([^"]+)"', block[:block.index(
        '\n\t(symbol "') if '\n\t(symbol "' in block else len(block)]))
    bad = []
    for key, value in (spec.get("rename") or {}).items():
        if str(key) not in numbers:
            bad.append(f"rename names pin {key}, which {symbol} does not have")
        if not str(value).strip():
            bad.append(f"rename gives pin {key} no name")
    return bad


def ask(ipn, description, mpn, prefix, folders, root):
    """Hand the libraries over and take back a symbol, or None."""
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT.format(
        ipn=ipn, description=description or "no description", mpn=mpn,
        prefix=prefix, out=out,
        folders="\n".join(f"    {f}" for f in folders))
    try:
        run = subprocess.run(
            ["claude", "-p", prompt,
             "--permission-mode", "acceptEdits",
             "--allowedTools", "Bash,Read,Write"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
            raise Bad(f"{ipn}: nothing written. " + " / ".join(tail))
        try:
            spec = json.load(open(out))
        except json.JSONDecodeError as exc:
            raise Bad(f"{ipn}: not JSON - {exc}")
    finally:
        if out.exists():
            out.unlink()

    bad = faults(spec, folders)
    if bad:
        raise Bad(f"{ipn}: " + "; ".join(bad))
    return spec


def find(con, board, ipn, extra=None):
    """The symbol for a part, with the renames it needs, or None. This is
    what `symbol-draw` calls before it draws anything."""
    row = con.execute("select description from parts_table where ipn = ?",
                      (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    mpns = part_numbers(con, ipn)
    classes = sibling("table-write").CLASSES
    prefix = classes[IPN.match(ipn).group(1)][1]
    spec = ask(ipn, row[0], mpns[0], prefix, folders(extra), repo_root(board))
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
        description = con.execute(
            "select description from parts_table where ipn = ?",
            (args.ipn,)).fetchone()
        if description is None:
            raise Bad(f"{args.ipn} is not in parts_table")
        mpns = part_numbers(con, args.ipn)
    finally:
        con.close()

    classes = sibling("table-write").CLASSES
    prefix = classes[IPN.match(args.ipn).group(1)][1]
    spec = ask(args.ipn, description[0], mpns[0], prefix,
               folders(args.lib), repo_root(board))

    if not spec.get("library"):
        print("null")
        print(f"    {spec.get('why', '')}")
        return 0
    print(f"{spec['library']}:{spec['symbol']}")
    for key, value in sorted((spec.get("rename") or {}).items(),
                             key=lambda kv: str(kv[0])):
        print(f"    rename pin {key} to {value}")
    print(f"    {spec.get('why', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
