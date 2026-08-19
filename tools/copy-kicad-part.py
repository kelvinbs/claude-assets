#!/usr/bin/env python3
"""copy-kicad-part - copy a KiCad symbol into the project library.

    copy-kicad-part.py <board-dir> <hint> [--name NAME] [--lib DIR ...]

Give it a part number, or a description. It finds the symbol in the KiCad
libraries, copies it into `lib/`, renames the pins to what the part calls
them, and prints the `<library>:<name>` it wrote. It prints `null` when no
library holds anything that is the part.

Five steps: take the hint, index the libraries, score the index for
candidates, ask once which of them is the part, write the answer into the
library. Only the fourth runs a model, and it reads nothing - the candidates
and their pins are in the prompt.

It calls `lib-index` as a command. It imports nothing from another tool.
"""

import argparse
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
            # stderr, so a tool reading this one's output reads its
            # answer and not its ticking.
            print(f"    {label}  {int(time.monotonic() - start)}s",
                  file=sys.stderr, flush=True)
    threading.Thread(target=tick, daemon=True).start()
    return stop


# ------------------------------------------------------------ 1, the project

def library_of(board):
    """The project's library, found where `sym-lib-table` points, or by the
    one `.kicad_sym` in `lib/`. The nickname is the project's, per section 2."""
    table = Path(board) / "sym-lib-table"
    if table.exists():
        for line in table.read_text().splitlines():
            name = re.search(r'\(name "([^"]+)"\)', line)
            uri = re.search(r'\(uri "\$\{KIPRJMOD\}/lib/([^"]+)"\)', line)
            if name and uri:
                path = Path(board) / "lib" / uri.group(1)
                if path.exists():
                    return name.group(1), path
    found = sorted((Path(board) / "lib").glob("*.kicad_sym"))
    if len(found) == 1:
        return found[0].stem, found[0]
    raise Bad(f"no project library in {board}/lib. Run kicad-init first")


# -------------------------------------------------------------- 2, the index

def index(board, extra):
    """`lib-index` builds it when it is missing or out of date. It is run as
    a command; nothing of it is imported."""
    argv = [sys.executable, str(HERE / "lib-index.py"), str(board)]
    for folder in extra or []:
        argv += ["--lib", folder]
    run = subprocess.run(argv, capture_output=True, text=True)
    if run.returncode != 0:
        raise Bad((run.stderr or run.stdout).strip())
    path = Path(board) / "lib" / "kicad-index.json"
    if not path.exists():
        raise Bad(f"{path} was not written")
    return json.load(open(path))["symbols"]


# --------------------------------------------------------- 3, the candidates

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
    for core in cores:
        if core and (flat.startswith(core) or core.startswith(flat)):
            points += 30
    text = words(row["description"]) | words(row["keywords"]) | words(flat)
    return points + 3 * len(terms & text)


def shortlist(rows, hint):
    tokens = [t for t in re.split(r"[\s,;]+", hint) if len(t) > 3]
    keys = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tokens]
    cores = [re.sub(r"[^a-z0-9]", "", re.split(r"[-/]", t)[0].lower())
             for t in tokens]
    terms = words(hint)
    scored = [(score(r, keys, cores, terms), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["library"], sr[1]["symbol"]))
    return [r for _, r in scored[:CANDIDATES]]


def as_lines(rows):
    out = []
    for row in rows:
        pins = row["pins"][:PINS_SHOWN]
        text = " ".join(f"{p['number']}:{p['name'] or '~'}" for p in pins)
        if len(row["pins"]) > PINS_SHOWN:
            text += " ..."
        out.append(f"    {row['library']}:{row['symbol']}  "
                   f"{row['pin_count']} pins  {row['description']}  [{text}]")
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


def top_level(src, name):
    """One symbol out of a library, brackets balanced, strings skipped so a
    bracket inside one opens nothing."""
    needle = f'(symbol "{name}"'
    start = src.find(needle)
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(src)):
        char = src[i]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif in_string:
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise Bad(f"symbol '{name}' does not close")


def parts_of(block):
    """Top-level sub-expressions, as (tag, text)."""
    out = []
    depth, in_string, escaped, start = 0, False, False, None
    body = block[block.index("(") + 1:]
    for i, char in enumerate(body):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif in_string:
            continue
        elif char == "(":
            if depth == 0:
                start = i
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                text = body[start:i + 1]
                out.append((text[1:].split(None, 1)[0].rstrip(")"), text))
            elif depth < 0:
                break
    return out


def flatten(block, src, name):
    """KiCad stores a derived symbol with its parent's graphics folded in. A
    copy that still carries `extends` points outside the library it landed
    in, and section 2 does not allow that."""
    found = re.search(r'\(extends "([^"]+)"\)', block)
    if not found:
        return block
    parent_name = found.group(1)
    parent = top_level(src, parent_name)
    if parent is None:
        raise Bad(f"'{name}' extends '{parent_name}', which is missing")
    parent = flatten(parent, src, parent_name)

    settings, props, bodies = {}, {}, []
    for who, source in (("parent", parent), ("child", block)):
        for tag, text in parts_of(source):
            if tag == "extends":
                continue
            if tag == "property":
                props[re.match(r'\(property "([^"]*)"', text).group(1)] = text
            elif tag == "symbol":
                if who == "parent":
                    bodies.append(
                        text.replace(f'"{parent_name}_', f'"{name}_', 1))
            else:
                settings[tag] = text
    head = block[:block.index("\n") + 1]
    return head + "".join("\t\t" + t + "\n"
                          for t in list(settings.values())
                          + list(props.values()) + bodies) + "\t)"


def set_property(block, name, value):
    """A copied symbol keeps its drawing and takes this project's fields."""
    pattern = re.compile(r'(\(property "%s" )"[^"]*"' % re.escape(name))
    if pattern.search(block):
        return pattern.sub(lambda m: m.group(1) + '"%s"' % value, block,
                           count=1)
    head = block[:block.index("\n") + 1]
    return (head + f'\t\t(property "{name}" "{value}"\n\t\t\t(at 0 0 0)\n'
            f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n'
            + block[len(head):])


def rename_pins(block, rename):
    """Give a pin the name the datasheet prints. The symbol is the part; only
    the label differs, and the label is what a person reads on the sheet."""
    for number, name in (rename or {}).items():
        pattern = re.compile(
            r'(\(pin\b(?:(?!\(pin\b).)*?\(name ")[^"]*("(?:(?!\(pin\b).)*?'
            r'\(number "%s")' % re.escape(str(number)), re.S)
        block, count = pattern.subn(
            lambda m: m.group(1) + str(name) + m.group(2), block, count=1)
        if not count:
            raise Bad(f"pin {number} is not in the symbol")
    return block


def write(library, name, block):
    """Add to the library, or replace what is there under this name. Every
    other symbol in it is left exactly as it is."""
    src = library.read_text()
    present = top_level(src, name)
    if present is not None:
        library.write_text(src.replace(present, block.strip(), 1))
        return
    close = src.rstrip().rfind(")")
    if close < 0:
        raise Bad(f"{library} does not read as a kicad_symbol_lib")
    library.write_text(src[:close] + block + src[close:])


def copy(library, nickname, spec, name, extra):
    path = source_path(spec["library"], extra)
    src = path.read_text(errors="replace")
    block = top_level(src, spec["symbol"])
    if block is None:
        raise Bad(f"{path} does not hold '{spec['symbol']}'")
    block = flatten(block, src, spec["symbol"])
    block = block.replace(f'(symbol "{spec["symbol"]}"', f'(symbol "{name}"', 1)
    block = block.replace(f'"{spec["symbol"]}_', f'"{name}_')
    block = rename_pins(block, spec.get("rename"))
    block = set_property(block, "Value", name)
    block = set_property(block, "Footprint", "")
    block = set_property(block, "origin",
                         f'{spec["library"]}:{spec["symbol"]}')
    write(library, name, "\t" + block.strip() + "\n")
    return f"{nickname}:{name}"


# ------------------------------------------------------------------------ run

def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("hint", help="a part number, or a description")
    ap.add_argument("--name", help="name the copy this. Defaults to the hint")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    nickname, library = library_of(board)
    name = args.name or re.sub(r"[^A-Za-z0-9_.-]", "_", args.hint)[:48]

    rows = shortlist(index(board, args.lib), args.hint)
    if not rows:
        print("null")
        print("    nothing in the index scored against the hint")
        return 0
    root = board.resolve()
    for folder in [root] + list(root.parents):
        if (folder / ".git").exists():
            root = folder
            break
    spec = ask(args.hint, rows, root)
    if not spec.get("library"):
        print("null")
        print(f"    {spec.get('why', '')}")
        return 0

    lib_id = copy(library, nickname, spec, name, args.lib)
    renames = spec.get("rename") or {}
    print(f"{lib_id}  ({spec.get('fit', '')})"
          + (f"  {len(renames)} pin(s) renamed" if renames else ""))
    print(f"    from {spec['library']}:{spec['symbol']}")
    print(f"    {spec.get('why', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
