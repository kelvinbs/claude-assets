#!/usr/bin/env python3
"""datasheet-read - read a pinout out of a datasheet.

    datasheet-read.py <board-dir> <ipn> [--datasheet PATH]
    datasheet-read.py <board-dir> --all

The first tool of process 2. It reads the part out of `board.db`, finds its
datasheet, and hands back the pins. `symbol-draw` calls it and draws them.
Run alone it prints them.

It writes no file of its own. The pins belong in the symbol, and the symbol
is where `symbol-draw` puts them.

There is no parser here, and there are two tiers - n9_1.38. Tier 1:
`pdftotext` extracts the pages that look like a pin table and one plain
text-in, JSON-out model completion reads them - no tools, no rendering,
seconds. Tier 2, only when tier 1 yields nothing - a scan, a figure-only
sheet: the full reader session that renders pages and looks at them.
What comes back is checked here either way, and a pinout that does not
hold up is thrown away, so a bad one never reaches a symbol.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import sys
import threading
import time
from pathlib import Path

PIN_TYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector",
    "open_emitter", "no_connect",
}
SIDES = {"L", "R", "T", "B"}

IPN = re.compile(r"^[A-Z]\d{4}$")
TIMEOUT = 900
LABEL = "reading the datasheet"

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
family — take that variant's pinout.

Before you finish, read your file back against the figure pin by pin. A
wrong pin number passes ERC, passes the netlist, passes layout, and is
found on the bench with a board in your hand.
"""


PROMPT_TEXT = """Below is text extracted from the datasheet of {mpn} —
the part is {ipn}, {description}; the file is {datasheet}.

Read the pin table and answer with ONLY a JSON object - no fences, no
prose:

    {{"pins": [[1, "GND", "power_in", "B"], [2, "RFIN", "input", "L"]]}}

    number   as printed on the package. Every pin, 1 to N, none missing
    name     as printed, exactly. Do not tidy it or expand it
    type     one of: input output bidirectional tri_state passive free
             unspecified power_in power_out open_collector open_emitter
             no_connect
    side     L R T B. Inputs left, outputs right, supplies top, grounds
             bottom, and use your judgement where that does not fit

An exposed pad is a pin. It numbers after the last numbered pin. Where
the part number names a package variant the text tabulates separately,
take {mpn}'s variant. If the text does not carry a complete pin table
for this part, answer exactly NONE.

{text}
"""


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
    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    return stop


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"datasheet-read: {message}")


# ------------------------------------------------------------------ the record

def connect(board):
    path = Path(board) / "board.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run init-pipeline first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table"} <= have:
        raise Bad(f"{path} is missing a table. Run init-pipeline")
    return con


def designed_mpn(con, ipn):
    """The part's MPN and recorded datasheet, off the part itself (n0.4)."""
    row = con.execute(
        "select mpn, datasheet from parts_table where ipn = ?",
        (ipn,)).fetchone()
    if row is None or not row[0]:
        raise Bad(f"{ipn} has no mpn. Set a part number first")
    return row


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


def prefix_run(a, b):
    """Shared leading characters of two flat strings."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def find_sheet(folder, mpn):
    """Tier 1 of n5.11 decides alone: the whole part number in a filename,
    unique hit wins. Every partial prefix-run match (family files:
    ADA4896-2_ADA4897, USB334x) is a LEAD, never a decision - string
    similarity cannot tell a datasheet from an app note. One path back =
    the tier-1 unique hit; a list back = leads for the model to
    arbitrate; None = nothing scored."""
    key = flat(mpn)
    if not key:
        return None
    scored = []
    files = sorted(p for p in folder.rglob("*")
                   if p.is_file() and p.suffix.lower() == ".pdf")
    for p in files:
        stem = flat(p.stem)
        if key in stem:
            run = len(key)      # the whole part number, hyphens aside
        else:
            tokens = [flat(t) for t in re.split(r"[^A-Za-z0-9]+", p.stem)]
            tokens.append(stem)     # the hyphen-blind whole name too
            run = max((prefix_run(t, key) for t in tokens if t), default=0)
        if run >= 6:
            scored.append((run, p))
    if not scored:
        return None
    exact = [p for run, p in scored if run == len(key)]
    if len(exact) == 1:
        return exact[0]    # tier 1: the whole part number, uniquely
    scored.sort(key=lambda rp: (-rp[0], str(rp[1])))
    return [p for _, p in scored]    # leads - the model arbitrates


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
    back to the part, so the second run needs no argument."""
    if given:
        path = Path(given)
        if not path.exists():
            raise Bad(f"{path} does not exist")
    elif recorded:
        path = Path(board) / recorded   # the project folder first (n2.10)
        if not path.exists():
            path = repo_root(board) / recorded
        if not path.exists():
            path = Path(recorded)
        if not path.exists():
            raise Bad(f"{ipn}: the record has {recorded} for {mpn}, "
                      f"and it is not on disk")
    else:
        where = sheet_folder(board, folder)
        found = find_sheet(where, mpn)
        if found is None or isinstance(found, list):
            found = arbitrate_sheet(where, mpn,
                                    found if isinstance(found, list) else None)
        if found is None:
            raise Bad(f"{ipn}: no datasheet found for {mpn}. "
                      f"Give --datasheet")
        path = found

    stored = as_recorded(path, board)
    if stored != recorded:
        con.execute("update parts_table set datasheet = ? where ipn = ?",
                    (stored, ipn))
        con.commit()
    return path, stored


ARBITRATE = """Which file in {folder} is the manufacturer datasheet for
part number {mpn}?

Files:
{listing}

Name-similarity leads (suggestions only, often wrong - an app note can
outscore the real datasheet):
{leads}

Open a file (Read) if the name alone does not settle it. A user manual,
devkit brief, app note or errata is not the datasheet. Answer by writing
the exact filename - nothing else - to {out}. If no file documents the
part, write NONE."""


def arbitrate_sheet(folder, mpn, candidates):
    """Tier 3 of n5.7: a tie or a zero-hit with files present goes to the
    model, which sees the listing and may open files. Returns a Path, or
    None when it answers NONE. An answer outside the folder is refused."""
    pool = sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() == ".pdf")
    names = [str(p.relative_to(folder)) for p in pool]
    if not names:
        return None
    leads = [str(p.relative_to(folder)) for p in (candidates or [])]
    handle, path = tempfile.mkstemp(suffix=".txt")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = ARBITRATE.format(folder=folder, mpn=mpn,
                              listing="\n".join(names),
                              leads="\n".join(leads) or "(none)", out=out)
    beat = heartbeat(f"{mpn} - arbitrating datasheet")
    try:
        subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Write,Read,Glob"],
            cwd=folder, capture_output=True, text=True, timeout=TIMEOUT)
        if not out.exists():
            raise Bad(f"{mpn}: datasheet arbitration wrote nothing")
        answer = out.read_text().strip()
    finally:
        beat.set()
        if out.exists():
            out.unlink()
    if answer == "NONE":
        return None
    chosen = folder / answer
    if answer not in names or not chosen.exists():
        raise Bad(f"{mpn}: arbitration named '{answer}', "
                  f"not a file in {folder}")
    return chosen


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


def candidate_pages(pdf):
    """The pages whose text looks like a pin table, extracted. Empty for a
    scan - there is no text layer to match."""
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                          text=True)
    m = re.search(r"Pages:\s+(\d+)", info.stdout or "")
    total = int(m.group(1)) if m else 0
    found = []
    for page in range(1, total + 1):
        run = subprocess.run(
            ["pdftotext", "-layout", "-f", str(page), "-l", str(page),
             str(pdf), "-"], capture_output=True, text=True)
        text = run.stdout or ""
        if re.search(r"(?i)pin\s*(configuration|function|descriptions?)"
                     r"|mnemonic", text):
            found.append((page, text))
        if len(found) >= 4:
            break
    return found


def read_text(ipn, description, mpn, pdf, stored, quiet=False):
    """Tier 1: the extracted text and one plain completion - no tools, no
    rendering. None when the text carries no table or the answer does not
    hold up."""
    pages = candidate_pages(pdf)
    if not pages:
        return None
    text = "\n".join(f"[page {n}]\n{t}" for n, t in pages)[:40000]
    prompt = PROMPT_TEXT.format(ipn=ipn,
                                description=description or "no description",
                                mpn=mpn, datasheet=stored, text=text)
    beat = heartbeat(f"{ipn} reading the text")
    try:
        run = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=300)
    finally:
        beat.set()
    try:
        envelope = json.loads(run.stdout)
        usage = envelope.get("usage") or {}
        spent = int(usage.get("input_tokens", 0)) \
            + int(usage.get("output_tokens", 0))
        answer = envelope.get("result") or ""
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None
    if answer.strip() == "NONE":
        return None
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        spec = json.loads(answer[start:end + 1])
    except json.JSONDecodeError:
        return None
    bad = faults(spec)
    if bad:
        if not quiet:
            for fault in bad:
                print(f"    {ipn}: text tier: {fault}")
        return None
    return {"pins": sorted(spec["pins"], key=lambda row: row[0]),
            "tokens": spent, "tier": 1}


def read(ipn, description, mpn, pdf, datasheet, root, quiet=False):
    """Tier 1 first; the rendered reader only when the text yields
    nothing."""
    spec = read_text(ipn, description, mpn, pdf, datasheet, quiet)
    if spec is not None:
        return spec
    return read_rendered(ipn, description, mpn, datasheet, root, quiet)


def read_rendered(ipn, description, mpn, datasheet, root, quiet=False):
    """Tier 2: the full reader session - renders pages and looks at them.

    The reader writes a file because that is how it is told what to produce.
    The file is scratch: it is read back here and deleted, and nothing of it
    reaches the project."""
    handle, path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    out = Path(path)
    out.unlink()
    prompt = PROMPT.format(ipn=ipn, description=description or "no description",
                           mpn=mpn, datasheet=datasheet, out=out)
    beat = heartbeat(f"{ipn} " + LABEL)
    try:
        run = subprocess.run(
            ["claude", "-p", prompt,
             "--permission-mode", "acceptEdits",
             "--allowedTools", "Bash,Read,Write,WebSearch,WebFetch",
             "--output-format", "json"],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        beat.set()
        try:
            usage = (json.loads(run.stdout).get("usage") or {})
            spent = int(usage.get("input_tokens", 0)) \
                + int(usage.get("output_tokens", 0))
        except (json.JSONDecodeError, ValueError, AttributeError):
            spent = 0

        if not out.exists():
            if not quiet:
                tail = (run.stdout or run.stderr).strip().splitlines()[-3:]
                print(f"    {ipn}: nothing written. " + " / ".join(tail))
            return None
        try:
            spec = json.load(open(out))
        except json.JSONDecodeError as exc:
            if not quiet:
                print(f"    {ipn}: not JSON - {exc}")
            return None
    finally:
        beat.set()
        if out.exists():
            out.unlink()

    bad = faults(spec)
    if bad:
        if not quiet:
            for fault in bad:
                print(f"    {ipn}: {fault}")
        return None
    return {"pins": sorted(spec["pins"], key=lambda row: row[0]),
            "tokens": spent, "tier": 2}


def part_file(con, board, ipn):
    """`design/parts/<IPN>-<name>.json` - the facts mined from the
    datasheet (n3.13). The name tail is for eyes; the IPN is the key."""
    row = con.execute("select name from parts_table where ipn = ?",
                      (ipn,)).fetchone()
    tail = f"-{row[0]}" if row and row[0] else ""
    return Path(board) / "parts" / f"{ipn}{tail}.json"


def pinout(con, board, ipn, datasheet=None, folder=None):
    """The pins of one part, and the datasheet they came from, or None.
    This is what `symbol-draw` calls."""
    ipn, description = part_row(con, ipn)
    # The part file is the cache - datasheet-read.md. Present, it is read;
    # absent, the datasheet is read and the file written (n3.13).
    part = part_file(con, board, ipn)
    if part.exists():
        held = json.loads(part.read_text())
        pins = held.get("pins") if isinstance(held, dict) else held
        if pins:
            return {"pins": pins, "datasheet": "", "tokens": 0,
                    "tier": 0, "cached": str(part)}
    mpn, recorded = designed_mpn(con, ipn)
    path, stored = resolve_sheet(con, board, ipn, mpn, recorded,
                                 datasheet, folder)
    spec = read(ipn, description, mpn, path, stored, repo_root(board))
    if spec is None:
        return None
    spec["datasheet"] = stored
    part.parent.mkdir(exist_ok=True)
    part.write_text(json.dumps({"pins": spec["pins"]}, indent=1))
    return spec


# ------------------------------------------------------------------------ run

def one(con, board, ipn, args):
    spec = pinout(con, board, ipn, args.datasheet, args.datasheets)
    if spec is None:
        if args.json:
            print(json.dumps({"ipn": ipn, "pins": None}))
        else:
            print(f"{ipn}  not read")
        return False
    if args.json:
        # What another tool reads. `symbol-draw` runs this as a command.
        print(json.dumps({"ipn": ipn, "datasheet": spec["datasheet"],
                          "pins": spec["pins"],
                          "tokens": spec.get("tokens", 0),
                          "tier": spec.get("tier", 0)}))
        return True
    print(f"{ipn}  {len(spec['pins'])} pins  {spec['datasheet']}  "
          f"tokens {spec.get('tokens', 0)}  tier {spec.get('tier', 0)}")
    for number, name, etype, side in spec["pins"]:
        print(f"    {number:>4}  {name:<16} {etype:<15} {side}")
    return True


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn", nargs="?")
    ap.add_argument("--all", action="store_true",
                    help="every part with no symbol yet")
    ap.add_argument("--datasheet", help="the PDF, when the name does not match")
    ap.add_argument("--datasheets", help="the directory to search")
    ap.add_argument("--json", action="store_true",
                    help="print the pins as JSON, for another tool to read")
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
        if args.all and len(targets) > 1:
            # n5.17: the reads share nothing - run them as a batch of
            # worker subprocesses, print each part's output whole as it
            # lands, with a progress line: timestamp, x of y, elapsed,
            # remaining, ETA.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from datetime import datetime, timedelta

            def run_one(ipn):
                argv = [sys.executable, __file__, str(board), ipn]
                if args.datasheets:
                    argv += ["--datasheets", args.datasheets]
                return ipn, subprocess.run(argv, capture_output=True,
                                           text=True, timeout=TIMEOUT)

            start = time.time()
            done = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(run_one, ipn) for ipn in targets]
                for future in as_completed(futures):
                    ipn, run = future.result()
                    done += 1
                    sys.stdout.write(run.stdout)
                    if run.returncode != 0:
                        sys.stdout.write(run.stderr)
                        failed.append(ipn)
                    elapsed = time.time() - start
                    remaining = (len(targets) - done) * elapsed / done
                    eta = datetime.now() + timedelta(seconds=remaining)
                    print(f"{datetime.now():%H:%M:%S}  {done} of "
                          f"{len(targets)}  elapsed {elapsed:.0f}s  "
                          f"remaining ~{remaining:.0f}s  ETA {eta:%H:%M:%S}",
                          flush=True)
            failed.sort()
        else:
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
