#!/usr/bin/env python3
"""copy-kicad-part - copy a KiCad symbol into the project library.

    copy-kicad-part.py <board-dir> <hint> [--ipn IPN] [--nickname N] [--lib DIR]

Give it a part number, or a description. It finds the symbol in the KiCad
libraries, copies it into `lib/`, renames the pins to what the part calls
them, and prints the `<nickname>:<name>` it wrote. It prints `null` when no
library holds anything that is the part.

Five steps: take the hint, index the libraries, score the index for
candidates, ask once which of them is the part, write the answer into the
library. Only the fourth runs a model, and it reads nothing - the candidates
and their pins are in the prompt.

Drawing a symbol is the last resort. This is asked first.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

TIMEOUT = 600
CANDIDATES = 24
PINS_SHOWN = 24      # a 100-pin MCU does not need its pins listed to be found

STOCK_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/share/kicad/symbols",
]

PROMPT = """Which of these KiCad symbols is the symbol for {hint}?

The candidates come from every library on this machine. Each line is
`library:symbol`, its pin count, its description, and its pins as
number:name.

{candidates}

A similar part's symbol is this part's symbol when its pins do the same job.
Take it, and rename the pins to the names this part's datasheet prints. The
package and the maker belong to the footprint, not the symbol - a candidate
in another package is not a reason to refuse.

Null when nothing listed has pins that do the same job. Pin count alone is
never the test. A part that is not on a schematic at all - a bare board, an
enclosure, a host the board plugs into - is null.

Write one of these to {out} and nothing else:

    {{"library": "as listed", "symbol": "as listed",
      "rename": {{"3": "VCC"}}, "fit": "exact, family or generic",
      "why": "one line"}}

    {{"library": null, "why": "one line - the nearest candidate and why it is not this part"}}

`rename` carries only the pins whose names differ. Leave it out when nothing
needs changing. It is not a way to reshape a symbol: if the pins are not the
part's pins, the answer is null.
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


# ------------------------------------------------------- 3, the candidates

WORD = re.compile(r"[a-z0-9]+")
NOISE = {"the", "and", "for", "one", "per", "with", "from", "its", "into",
         "board", "side", "each", "own", "carries", "shared", "same", "this"}


def words(text):
    return {w for w in WORD.findall((text or "").lower())
            if len(w) > 2 and w not in NOISE}


def score(row, keys, cores, terms):
    """The part number first, then the words of the hint."""
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
    return points + 3 * len(terms & text)


def shortlist(rows, hint):
    tokens = [t for t in re.split(r"[\s,;]+", hint) if t]
    keys = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tokens]
    keys = [k for k in keys if len(k) > 3]
    cores = [re.sub(r"[^a-z0-9]", "", re.split(r"[-/]", t)[0].lower())
             for t in tokens]
    cores = [c for c in cores if len(c) > 3]
    terms = words(hint)
    scored = [(score(r, keys, cores, terms), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["library"], sr[1]["symbol"]))
    return [r for _, r in scored[:CANDIDATES]]


def as_lines(rows):
    out = []
    for r in rows:
        pins = r["pins"][:PINS_SHOWN]
        text = " ".join(f"{p['number']}:{p['name'] or '~'}" for p in pins)
        if len(r["pins"]) > PINS_SHOWN:
            text += " ..."
        out.append(f"    {r['library']}:{r['symbol']}  {r['pin_count']} pins"
                   f"  {r['description']}  [{text}]")
    return "\n".join(out)


# ------------------------------------------------------------- 4, the asking

def faults(spec, rows):
    if spec.get("library") in (None, ""):
        return []
    name, symbol = spec.get("library"), spec.get("symbol")
    if not isinstance(name, str) or not isinstance(symbol, str) or not symbol:
        return ["a library named without a symbol"]
    match = [r for r in rows if r["library"] == name and r["symbol"] == symbol]
    if not match:
        return [f"{name}:{symbol} was not one of the candidates"]
    numbers = {p["number"] for p in match[0]["pins"]}
    bad = []
    for key, value in (spec.get("rename") or {}).items():
        if str(key) not in numbers:
            bad.append(f"rename names pin {key}, not in {symbol}")
        if not str(value).strip():
            bad.append(f"rename gives pin {key} no name")
    return bad


def ask(hint, rows, root):
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT.format(hint=hint, candidates=as_lines(rows), out=out)
    beat = heartbeat(f"{hint[:40]} - asking")
    try:
        run = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Write"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
            raise Bad("nothing written. " + " / ".join(tail))
        try:
            spec = json.load(open(out))
        except json.JSONDecodeError as exc:
            raise Bad(f"not JSON - {exc}")
    finally:
        beat.set()
        if out.exists():
            out.unlink()

    bad = faults(spec, rows)
    if bad:
        raise Bad("; ".join(bad))
    return spec


# ------------------------------------------------------------ 5, the writing

def find_stock():
    for candidate in [os.environ.get("KICAD_SYMBOL_DIR")] + STOCK_CANDIDATES:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    raise Bad("KiCad symbol directory not found. Set KICAD_SYMBOL_DIR")


def source_path(nickname, extra):
    for folder in [find_stock()] + [Path(d) for d in (extra or [])]:
        path = folder / f"{nickname}.kicad_sym"
        if path.exists():
            return path
    raise Bad(f"no library '{nickname}' on disk")


def copy(board, nickname, spec, name, extra=None):
    """The symbol, copied in under `name` with its pins renamed. Returns the
    library id it wrote."""
    sym = sibling("symbol-draw")
    path = source_path(spec["library"], extra)
    block = sym.extract_symbol(path, spec["symbol"])
    if block is None:
        raise Bad(f"{path} does not hold '{spec['symbol']}'")
    block = sym.flatten_extends(block, path, spec["symbol"])
    block = block.replace(f'(symbol "{spec["symbol"]}"', f'(symbol "{name}"', 1)
    block = block.replace(f'"{spec["symbol"]}_', f'"{name}_')
    block = sym.rename_pins(block, spec.get("rename"))
    block = sym.set_property(block, "Value", name)
    block = sym.set_property(block, "Footprint", "")
    block = sym.set_property(
        block, "origin", f'{spec["library"]}:{spec["symbol"]}')
    library = Path(board) / "lib" / f"{nickname}.kicad_sym"
    if not library.exists():
        raise Bad(f"{library} does not exist. Run lib-init first")
    sym.merge(library, name, "\t" + block.strip() + "\n", True)
    return f"{nickname}:{name}"


# ------------------------------------------------------------------------ run

def take(board, hint, name, nickname, extra=None):
    """The five steps. Returns (lib_id, spec) or (None, spec)."""
    rows = sibling("lib-index").build(board, extra)
    candidates = shortlist(rows, hint)
    if not candidates:
        return None, {"library": None, "why": "nothing in the index scored"}
    spec = ask(hint, candidates, repo_root(board))
    if not spec.get("library"):
        return None, spec
    return copy(board, nickname, spec, name, extra), spec


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("hint", help="a part number, or a description")
    ap.add_argument("--ipn", help="name the copy this, rather than the hint")
    ap.add_argument("--nickname", help="the project library. Defaults to the "
                                       ".kicad_pro name")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    try:
        nickname = sibling("lib-init").nickname_of(board, args.nickname)
    except SystemExit as exc:
        raise Bad(str(exc))

    name = args.ipn or re.sub(r"[^A-Za-z0-9_.-]", "_", args.hint)[:48]
    lib_id, spec = take(board, args.hint, name, nickname, args.lib)
    if lib_id is None:
        print("null")
        print(f"    {spec.get('why', '')}")
        return 0
    renames = spec.get("rename") or {}
    print(f"{lib_id}  ({spec.get('fit', '')})"
          + (f"  {len(renames)} pin(s) renamed" if renames else ""))
    print(f"    from {spec['library']}:{spec['symbol']}")
    print(f"    {spec.get('why', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
