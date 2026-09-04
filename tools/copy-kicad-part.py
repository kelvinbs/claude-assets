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
import shutil
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


# ------------------------------------------------------------ the part file

def part_file(board, name):
    """`parts/<IPN>-<name>.json`, found by the name (datasheet-read.md T1)."""
    found = sorted((Path(board) / "parts").glob(f"*-{name}.json"))
    return found[0] if len(found) == 1 else None


def part_file_set(board, name, key, value):
    """A key onto the part file, when the part has one. Nothing else in it
    is touched."""
    path = part_file(board, name)
    if path is None:
        return False
    held = json.load(open(path))
    held[key] = value
    path.write_text(json.dumps(held, indent=1))
    return True


# ------------------------------------------------------------ the footprints

MODEL_DIRS = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels",
    "/usr/share/kicad/3dmodels",
    "/usr/local/share/kicad/3dmodels",
    "C:/Program Files/KiCad/share/kicad/3dmodels",
]


def index_footprints(board, extra):
    """The footprint rows of the index, and the `.pretty` folders they came
    from. `lib-index` builds it, run as a command."""
    argv = [sys.executable, str(HERE / "lib-index.py"), str(board)]
    for folder in extra or []:
        argv += ["--lib", folder]
    run = subprocess.run(argv, capture_output=True, text=True)
    if run.returncode != 0:
        raise Bad((run.stderr or run.stdout).strip())
    held = json.load(open(Path(board) / "lib" / "kicad-lib-index.json"))
    return held.get("footprints") or [], held.get("footprint_libraries") or []


def footprint_score(row, package, dims, pins):
    """The part file's `package` and `package_dims` against the footprint's
    name, description and tags; its pin count against the pad count - an
    exposed pad is often several paste pads. Parsing, no judgment."""
    keys = [k for k in re.split(r"[^a-z0-9.]+", package.lower()) if k]
    dims = dims or {}
    if dims.get("length") and dims.get("width"):
        keys.append(f"{dims['length']:g}x{dims['width']:g}mm")
    if dims.get("pitch"):
        keys.append(f"p{dims['pitch']:g}mm")
    name = row["footprint"].lower()
    text = (row["description"] + " " + row["tags"]).lower()
    score = 0
    for k in keys:
        if k in name:
            score += 3
        elif k in text:
            score += 1
    if pins:
        pads = row["pad_count"]
        if pads == pins:
            score += 5
        elif abs(pads - pins) == 1 or pins < pads <= pins + 6:
            score += 3
    return score


def footprint_shortlist(rows, package, dims, pins, limit=25):
    scored = sorted(((footprint_score(r, package, dims, pins), r)
                     for r in rows), key=lambda t: -t[0])
    return [r for s, r in scored[:limit] if s > 0]


def model_source(model):
    """`${KICAD*_3DMODEL_DIR}/lib.3dshapes/file` resolved against the
    installed model folders. None when no file is there."""
    if not model:
        return None
    tail = re.sub(r"^\$\{[^}]+\}/", "", model)
    for folder in [os.environ.get("KICAD_3DMODEL_DIR")] + MODEL_DIRS:
        if folder and (Path(folder) / tail).is_file():
            return Path(folder) / tail
    return None


def copy_footprint(board, nickname, lib_name, fp_name, name, fp_libs, row):
    """The `.kicad_mod` into `lib/<nickname>.pretty/<name>.kicad_mod`, its
    model into `lib/3d/`, the model path rewritten per section 3.3. Nothing
    in the footprint is modified beyond its name and that path."""
    src = None
    for folder in fp_libs:
        folder = Path(folder)
        if folder.name == f"{lib_name}.pretty":
            src = folder / f"{fp_name}.kicad_mod"
    if src is None or not src.is_file():
        raise Bad(f"{lib_name}:{fp_name} is not in the footprint libraries")
    text = src.read_text(errors="replace")
    text = re.sub(r'\(footprint "[^"]*"', f'(footprint "{name}"', text, count=1)
    model_note = "no model named"
    source = model_source(row.get("model", ""))
    if row.get("model") and source is None:
        model_note = f"model not installed: {row['model']}"
        text = re.sub(r'\n\s*\(model "[^"]*"[\s\S]*?\n\t\)', "", text, count=1)
    elif source is not None:
        three_d = Path(board) / "lib" / "3d"
        three_d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, three_d / source.name)
        text = re.sub(r'\(model "[^"]*"',
                      f'(model "${{KIPRJMOD}}/lib/3d/{source.name}"', text,
                      count=1)
        model_note = f"model lib/3d/{source.name}"
    pretty = Path(board) / "lib" / f"{nickname}.pretty"
    if not pretty.is_dir():
        raise Bad(f"no {pretty}. Run init-pipeline first")
    (pretty / f"{name}.kicad_mod").write_text(text)
    return f"{nickname}:{name}", model_note


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


PIN_RE = re.compile(r'\n(\t+)\(pin\b.*?\n\1\)', re.S)
SIDE_ANGLE = {"L": 0, "R": 180, "B": 90, "T": 270}


def pin_entries(block):
    """Every pin block: (span, number, x, y, angle)."""
    out = []
    for m in PIN_RE.finditer(block):
        t = m.group(0)
        num = re.search(r'\(number "([^"]*)"', t)
        at = re.search(r'\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)', t)
        out.append((m.span(), num.group(1) if num else "",
                    float(at.group(1)), float(at.group(2)),
                    float(at.group(3))))
    return out


def pin_sexp(number, pname, etype, x, y, angle, depth):
    pad = "\t" * depth
    eff = f"{pad}\t\t(effects\n{pad}\t\t\t(font\n{pad}\t\t\t\t(size 1.27 1.27)\n{pad}\t\t\t)\n{pad}\t\t)\n"
    return (f"\n{pad}(pin {etype} line\n"
            f"{pad}\t(at {x:g} {y:g} {angle:g})\n"
            f"{pad}\t(length 2.54)\n"
            f"{pad}\t(name \"{pname}\"\n{eff}{pad}\t)\n"
            f"{pad}\t(number \"{number}\"\n{eff}{pad}\t)\n"
            f"{pad})")


def relayout(block, want):
    """Strip the donor's sub-symbols and draw one body sized to the pinout,
    every pin on its side. Layout and sizes are symbol-draw's."""
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("symbol_draw",
                                                   HERE / "symbol-draw.py")
    sd = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(sd)

    def key(v):
        return (0, int(v)) if v.isdigit() else (1, v)
    spec = {"pins": [[n, *want[n]] for n in sorted(want, key=key)]}
    half_w, half_h, sides, top = sd.body_size(spec)
    m = re.search(r'\n(\t+)\(symbol "', block)
    depth = len(m.group(1)) if m else 2
    pad = "\t" * depth
    body = re.sub(r'\n' + pad + r'\(symbol "[^"]*"\n.*?\n' + pad + r'\)',
                  "", block, flags=re.S)
    name = re.match(r'\s*\(symbol "([^"]+)"', body).group(1)
    G, L = sd.GRID, sd.PIN_LEN
    pins = ""
    for i, (n, pname, etype, _) in enumerate(sides["L"]):
        pins += pin_sexp(n, pname, etype, -half_w - L,
                         sd.snap(half_h - top - i * G), 0, depth + 1)
    for i, (n, pname, etype, _) in enumerate(reversed(sides["R"])):
        pins += pin_sexp(n, pname, etype, half_w + L,
                         sd.snap(half_h - top - i * G), 180, depth + 1)
    for i, (n, pname, etype, _) in enumerate(sides["T"]):
        pins += pin_sexp(n, pname, etype, sd.snap(-half_w + 2 * G + i * 2 * G),
                         half_h + L, 270, depth + 1)
    for i, (n, pname, etype, _) in enumerate(sides["B"]):
        pins += pin_sexp(n, pname, etype, sd.snap(-half_w + 2 * G + i * 2 * G),
                         -half_h - L, 90, depth + 1)
    q = pad + "\t"
    rect = (f"\n{q}(rectangle\n{q}\t(start {-half_w:g} {half_h:g})\n"
            f"{q}\t(end {half_w:g} {-half_h:g})\n"
            f"{q}\t(stroke\n{q}\t\t(width 0.254)\n{q}\t\t(type default)\n{q}\t)\n"
            f"{q}\t(fill\n{q}\t\t(type background)\n{q}\t)\n{q})")
    subs = (f"\n{pad}(symbol \"{name}_0_1\"{rect}\n{pad})"
            f"\n{pad}(symbol \"{name}_1_1\"{pins}\n{pad})")
    body = body.rstrip()
    assert body.endswith(")")
    return body[:-1].rstrip() + subs + "\n" + "\t" * (depth - 1) + ")"


def apply_pinout(block, pinout, name):
    """Pins renumbered, renamed, added and deleted to match the pinout -
    copy-kicad-part.md. Deterministic; the model only chose the symbol."""
    want = {str(n): (str(pname), etype, side)
            for n, pname, etype, side in pinout}
    entries = pin_entries(block)
    have = [e[1] for e in entries]
    # Renumber: same count, different sets - donor pins in sorted order
    # take the pinout's numbers in sorted order.
    if len(have) == len(want) and set(have) != set(want):
        order = sorted(set(have), key=lambda v: (len(v), v))
        target = sorted(want, key=lambda v: (len(v), v))
        for old, new in zip(order, target):
            block = re.sub(r'\(number "%s"' % re.escape(old),
                           '(number "@@%s"' % new, block, count=1)
        block = block.replace('(number "@@', '(number "')
        entries = pin_entries(block)
        have = [e[1] for e in entries]
    # Delete extras, highest span first.
    for (a, b), num, *_ in sorted(entries, key=lambda e: -e[0][0]):
        if num not in want:
            block = block[:a] + block[b:]
    entries = pin_entries(block)
    have = {e[1] for e in entries}
    # Add the missing: the body is re-laid to hold every pin, each on its
    # part-file side - symbol-draw's layout, so an added pin lands where a
    # drawn one would. A stacked pin is not a symbol (n2.39).
    if [n for n in want if n not in have]:
        block = relayout(block, want)
    return block


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


def show_pins(block):
    """No hidden pins - T2.10 rule, n9_1.21. A `(hide yes)` at the top
    level of a pin block is removed as the symbol is copied in, so every
    pin of every unit is visible on the sheet."""
    out, i = [], 0
    while True:
        j = block.find("(pin ", i)
        if j < 0:
            out.append(block[i:])
            return "".join(out)
        depth, k, in_str = 0, j, False
        while k < len(block):
            c = block[k]
            if in_str:
                if c == "\\":
                    k += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        pin = re.sub(r"\n\s*\(hide yes\)", "", block[j:k])
        out.append(block[i:j])
        out.append(pin)
        i = k
def write(library, name, block):
    block = show_pins(block)
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


def copy(library, nickname, spec, name, extra, pins=None, pinout=None):
    path = source_path(spec["library"], extra)
    src = path.read_text(errors="replace")
    block = top_level(src, spec["symbol"])
    if block is None:
        raise Bad(f"{path} does not hold '{spec['symbol']}'")
    block = flatten(block, src, spec["symbol"])
    numbers = set(re.findall(r'\(number "([^"]*)"', block))
    if pinout:
        # The pinout is applied whole - pins renumbered, renamed, added
        # and deleted to match it (editprop008). Pin modification is copy
        # work, the same class of edit as renaming the symbol.
        block = apply_pinout(block, pinout, name)
        numbers = set(re.findall(r'\(number "([^"]*)"', block))
        spec = dict(spec)
        spec["rename"] = {str(n): pname for n, pname, *_ in pinout}
        spec["unused"] = []
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

def footprint_main(board, nickname, args):
    """Show, choose, take on the footprint rows - copy-kicad-part.md,
    Footprints."""
    name = args.footprint
    path = part_file(board, name)
    if path is None:
        raise Bad(f"{name}: no part file under parts/")
    held = json.load(open(path))
    package = held.get("package")
    dims = held.get("package_dims") or {}
    pins = len(held.get("pins") or [])
    if not package:
        print(f"{name}  null")
        print("    no package in the part file")
        return 0
    started = time.monotonic()
    rows, fp_libs = index_footprints(board, args.lib)
    if not args.take:
        print(f"== {name}: {package}, {pins} pins")
        for r in footprint_shortlist(rows, package, dims, pins):
            print(f"    {r['library']}:{r['footprint']}  {r['pad_count']} pads"
                  f"  {r['description'][:70]}")
        print("choose, then rerun with --take LIBRARY:FOOTPRINT")
        return 0
    lib_name, _, fp_name = args.take.partition(":")
    if not fp_name:
        raise Bad("--take is LIBRARY:FOOTPRINT")
    row = next((r for r in rows if r["library"] == lib_name
                and r["footprint"] == fp_name), None)
    if row is None:
        print(f"{name}  null")
        print(f"    {args.take} is not in the index")
        return 0
    lib_id, model_note = copy_footprint(board, nickname, lib_name, fp_name,
                                        name, fp_libs, row)
    part_file_set(board, name, "footprint_donor", f"{lib_name}:{fp_name}")
    print(f"{name}  {lib_id}  (taken)  {model_note}")
    print(f"    from {lib_name}:{fp_name}")
    print(f"kpi parts 1 tokens 0 tokens/part 0 "
          f"elapsed {time.monotonic() - started:.1f}s")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("hint", nargs="?",
                    help="a part number, or a description")
    ap.add_argument("--ipn", help="the written symbol's name - the part's name, per "
                         "n0.3. Defaults to the hint")
    ap.add_argument("--nickname",
                    help="the project library's nickname, when no "
                         ".kicad_pro names it")
    ap.add_argument("--pins", type=int,
                    help="the part's pin count - a copy with any other "
                         "count is refused")
    ap.add_argument("--take",
                    help="LIBRARY:SYMBOL - the choice, made in-session. "
                         "Without it the shortlist is printed and the run "
                         "stops")
    ap.add_argument("--pinout",
                    help="JSON file of [[number, name, type, side], ...] - "
                         "the datasheet's pinout. Sets the count, and the "
                         "pins are renamed to these names by number")
    ap.add_argument("--batch",
                    help='JSON list of {"name", "hint", "pins"?} - one '
                         'run, one ask')
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    ap.add_argument("--footprint", metavar="NAME",
                    help="show, or with --take copy, a footprint for the "
                         "part of this name - by its part file's package")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    nickname, library = library_of(board, args.nickname)

    if args.footprint:
        return footprint_main(board, nickname, args)

    if args.batch:
        parts = json.load(open(args.batch))
        for p in parts:
            if not p.get("name") or not p.get("hint"):
                raise Bad("--batch entries carry a name and a hint")
    elif args.hint:
        name = args.ipn or re.sub(r"[^A-Za-z0-9_.-]", "_", args.hint)[:48]
        pinout = json.load(open(args.pinout)) if args.pinout else None
        parts = [{"name": name, "hint": args.hint, "pins": args.pins,
                  "pinout": pinout}]
    else:
        raise Bad("a hint, or --batch")

    rows = index(board, args.lib)

    root = board.resolve()
    for folder in [root] + list(root.parents):
        if (folder / ".git").exists():
            root = folder
            break

    lists = {p["name"]: retrieve(rows, p["hint"], [])
             for p in parts}

    # No second session (1.3: the LLM performing the stage is the one
    # running it). Without --take, the shortlist is printed and the run
    # stops; the running LLM chooses and reruns with --take.
    if not args.take:
        for p in parts:
            print(f"== {p['name']}: {p['hint']}")
            print(as_lines(lists[p["name"]]) or "    (nothing scored)")
        print("choose, then rerun with --take LIBRARY:SYMBOL")
        return 0
    if len(parts) != 1:
        raise Bad("--take decides one part - run one at a time")
    lib_name, _, sym_name = args.take.partition(":")
    if not sym_name:
        raise Bad("--take is LIBRARY:SYMBOL")
    specs = {parts[0]["name"]: {"library": lib_name, "symbol": sym_name,
                                "rename": {}, "unused": [],
                                "fit": "taken",
                                "why": "chosen in-session"}}

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
            lib_id = copy(library, nickname, spec, name, args.lib,
                          p.get("pins"), p.get("pinout"))
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
        part_file_set(board, name, "symbol_donor",
                      f"{spec['library']}:{spec['symbol']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
