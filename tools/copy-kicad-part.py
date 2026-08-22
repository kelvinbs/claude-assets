#!/usr/bin/env python3
"""copy-kicad-part - copy KiCad symbols into the project library.

    copy-kicad-part.py <board-dir> <hint> [--ipn IPN] [--nickname N] [--lib DIR ...]
    copy-kicad-part.py <board-dir> --batch FILE [--lib DIR ...]

Give it a part number or a description, or a batch of them - a JSON list of
{"name": ..., "hint": ...}. It finds each part's symbol in the KiCad
libraries, copies it into `lib/`, renames the pins to what the part calls
them, and prints one line per part: `<name>  <library>:<name>`, or
`<name>  null` when no library holds anything that is the part.

Two model calls per run, never per part. Call A reads every hint and emits
search queries - synonyms, family names, class fallbacks. Deterministic
retrieval runs every query over the index and unions the hits with the base
shortlist, so a generic symbol is always reachable. Call B picks each
part's symbol from its union, or nulls. A single hint is a batch of one.

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
QUERY_TOP = 8        # rows each query contributes
UNION_CAP = 40       # candidates a part may carry into the ask
PINS_SHOWN = 24      # a 100-pin MCU does not need its pins listed to be found

STOCK_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/share/kicad/symbols",
]

PROMPT = """You are choosing KiCad schematic symbols the way an engineer
would. For each part below, pick the symbol to copy for it.

Each part is a heading `== <name>: <hint>`, then leads - candidate lines
`library:symbol`, pin count, description, pins as number:name. Leads are
hints, not the menu: you may pick ANY symbol in the libraries.

{sections}

Sources you may read (read-only):
- The index: {index} - every symbol, its description and pins
- The libraries: {stock} - one .kicad_sym per library. A symbol with
  `(extends "PARENT")` takes its pins from PARENT in the same file - read
  the parent when a lead shows 0 pins.

Judgment rules:
- A similar part's symbol is this part's symbol when its pins can be
  mapped to the part's pins. Rename covers different pin names AND
  different pin functions - that is what rename is for.
- Frequency band, package, and maker never disqualify - they belong to
  the footprint.
- exact > family > generic. A generic symbol (Device:R, Device:Antenna,
  Connector:Conn_Coaxial, an RF gain block with bias) is a valid answer
  at fit "generic".
- A BOM row that carries a schematic page IS on the schematic. Null for
  no-schematic-presence only when the part truly never appears (a bare
  board, a bench host).
- A candidate with SPARE pins may be taken: list the spare pin numbers in
  "unused" and they are parked as NC. A candidate MISSING pins the part
  needs is not fixable - do not invent pins.
- Null only when no symbol's pins can be mapped, after actually looking.
- The floor: a symbol is the part's only when its pins genuinely do the
  part's jobs. When nothing visible qualifies, answer null - a wrong
  symbol is worse than none. Never rename an unrelated device into
  shape.

Write ONE json object to {out} - keys the part names, values:
  {{"library": ..., "symbol": ..., "rename": {{"3": "VCC"}},
    "unused": ["7", "8"], "fit": "exact, family or generic",
    "why": one short line}}
Null is {{"library": "", "why": ...}}. `rename` carries only pins whose
names change; `unused` only spare pins. Nothing but the json object in
the file. Verify a symbol you name outside the leads by reading its pins
in the library file first."""


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

def library_of(board, nickname=None):
    """The project's library, found where `sym-lib-table` points, or by the
    one `.kicad_sym` in `lib/`. The nickname is the project's, per section 2.
    `--nickname` names it directly when no `.kicad_pro` does."""
    if nickname:
        path = Path(board) / "lib" / f"{nickname}.kicad_sym"
        if not path.exists():
            raise Bad(f"no {path}. Run init-pipeline first")
        return nickname, path
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
    raise Bad(f"no project library in {board}/lib. Run init-pipeline first")


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
    path = Path(board) / "lib" / "kicad-lib-index.json"
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
        elif key:
            common = 0
            for a, b in zip(flat, key):
                if a != b:
                    break
                common += 1
            if common >= 6:
                points += 4 * common
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


# ---------------------------------------------------- 3a, the query expansion

PROMPT_QUERIES = """For each part below, write search queries that would find
its schematic symbol in the KiCad libraries.

Each part is one line: `== <name>: <hint>`.

{sections}

Per part, 3 to 8 short queries: the part number and its family root, close
pin-compatible parts, the device class in KiCad's own vocabulary (op-amp,
LNA, MMIC amplifier, SPDT switch, accelerometer, GNSS module, TCXO, VCO,
coaxial connector, test point, resistor, antenna...), and - when a generic
library symbol could stand in - the generic name (R, C, L, Antenna,
Conn_Coaxial, TestPoint, Jumper). Queries are search text, not sentences.

Write ONE json object to {out} - keys the part names, values arrays of query
strings. Nothing else. Do not read any file."""


def ask_queries(parts, root):
    """Model call A: per-part search queries. On any failure the run falls
    back to the base shortlist alone - retrieval still works, just narrower."""
    sections = "\n".join(f"== {p['name']}: {p['hint']}" for p in parts)
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT_QUERIES.format(sections=sections, out=out)
    beat = heartbeat(f"{len(parts)} part(s) - expanding queries")
    try:
        subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Write"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            return {}
        try:
            got = json.load(open(out))
        except json.JSONDecodeError:
            return {}
    finally:
        beat.set()
        if out.exists():
            out.unlink()
    if not isinstance(got, dict):
        return {}
    return {k: [q for q in v if isinstance(q, str) and q.strip()]
            for k, v in got.items() if isinstance(v, list)}


def retrieve(rows, hint, queries):
    """The base shortlist plus each query's best rows, deduplicated, capped."""
    seen = {}
    for row in shortlist(rows, hint):
        seen.setdefault((row["library"], row["symbol"]), row)
    for query in queries:
        tokens = [t for t in re.split(r"[\s,;]+", query) if len(t) > 1]
        keys = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tokens]
        cores = [re.sub(r"[^a-z0-9]", "", re.split(r"[-/]", t)[0].lower())
                 for t in tokens]
        terms = words(query)
        scored = [(score(r, keys, cores, terms), r) for r in rows]
        scored = [(sc, r) for sc, r in scored if sc > 0]
        scored.sort(key=lambda sr: (-sr[0], sr[1]["library"], sr[1]["symbol"]))
        for _, row in scored[:QUERY_TOP]:
            if len(seen) >= UNION_CAP:
                break
            seen.setdefault((row["library"], row["symbol"]), row)
    return list(seen.values())


# ------------------------------------------------------------- 4, the asking

def faults(spec, rows):
    """Shape checks only. Pin-level checks run in copy(), against the
    source block itself - the answer may name a symbol outside the leads."""
    if spec.get("library") in (None, ""):
        return []
    name, symbol = spec.get("library"), spec.get("symbol")
    if not isinstance(name, str) or not isinstance(symbol, str) or not symbol:
        return ["a library named without a symbol"]
    bad = []
    for key, value in (spec.get("rename") or {}).items():
        if not str(value).strip():
            bad.append(f"rename gives pin {key} no name")
    return bad


def ask(parts, lists, root, board):
    """One model call for every part in the run. `parts` is the batch,
    `lists` its leads by name. Returns the JSON object, keyed by name."""
    sections = "\n\n".join(
        f"== {p['name']}: {p['hint']}\n"
        + (as_lines(lists[p["name"]]) or "    (no leads scored - search the index)")
        for p in parts)
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT.format(sections=sections, out=out,
                           index=Path(board) / "lib" / "kicad-lib-index.json",
                           stock=find_stock())
    beat = heartbeat(f"{len(parts)} part(s) - asking")
    try:
        run = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Write,Read,Grep,Glob"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
            raise Bad("nothing written. " + " / ".join(tail))
        try:
            specs = json.load(open(out))
        except json.JSONDecodeError as exc:
            raise Bad(f"not JSON - {exc}")
    finally:
        beat.set()
        if out.exists():
            out.unlink()
    if not isinstance(specs, dict):
        raise Bad("the answer is not an object keyed by part name")
    return specs


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
    numbers = set(re.findall(r'\(number "([^"]*)"', block))
    for key in list((spec.get("rename") or {})) + list(spec.get("unused") or []):
        if str(key) not in numbers:
            raise Bad(f"{name}: pin {key} is not in "
                      f"{spec['library']}:{spec['symbol']}")
    block = block.replace(f'(symbol "{spec["symbol"]}"', f'(symbol "{name}"', 1)
    block = block.replace(f'"{spec["symbol"]}_', f'"{name}_')
    block = rename_pins(block, spec.get("rename"))
    block = rename_pins(block, {n: "NC" for n in (spec.get("unused") or [])})
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
    ap.add_argument("hint", nargs="?",
                    help="a part number, or a description")
    ap.add_argument("--ipn", help="name the copy this. Defaults to the hint")
    ap.add_argument("--nickname",
                    help="the project library's nickname, when no "
                         ".kicad_pro names it")
    ap.add_argument("--batch",
                    help='JSON list of {"name", "hint"} - one run, one ask')
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    nickname, library = library_of(board, args.nickname)

    if args.batch:
        parts = json.load(open(args.batch))
        for p in parts:
            if not p.get("name") or not p.get("hint"):
                raise Bad("--batch entries carry a name and a hint")
    elif args.hint:
        name = args.ipn or re.sub(r"[^A-Za-z0-9_.-]", "_", args.hint)[:48]
        parts = [{"name": name, "hint": args.hint}]
    else:
        raise Bad("a hint, or --batch")

    rows = index(board, args.lib)

    root = board.resolve()
    for folder in [root] + list(root.parents):
        if (folder / ".git").exists():
            root = folder
            break

    queries = ask_queries(parts, root)
    lists = {p["name"]: retrieve(rows, p["hint"], queries.get(p["name"], []))
             for p in parts}

    specs = ask(parts, lists, root, board)

    misses = 0
    for p in parts:
        name = p["name"]
        spec = specs.get(name) or {}
        bad = faults(spec, lists[name])
        if bad:
            # An unusable answer is a null with its reason, not a crash -
            # the caller's next resort (drawing) must still get its turn.
            print(f"{name}  null")
            print(f"    answer rejected: " + "; ".join(bad))
            misses += 1
            continue
        if not spec.get("library"):
            print(f"{name}  null")
            print(f"    {spec.get('why', '')}")
            misses += 1
            continue
        try:
            lib_id = copy(library, nickname, spec, name, args.lib)
        except Bad as exc:
            print(f"{name}  null")
            print(f"    answer rejected: {exc}")
            misses += 1
            continue
        renames = spec.get("rename") or {}
        parked = spec.get("unused") or []
        print(f"{name}  {lib_id}  ({spec.get('fit', '')})"
              + (f"  {len(renames)} pin(s) renamed" if renames else "")
              + (f"  {len(parked)} pin(s) unused" if parked else ""))
        print(f"    from {spec['library']}:{spec['symbol']}")
        print(f"    {spec.get('why', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
