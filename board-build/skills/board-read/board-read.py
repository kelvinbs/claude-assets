#!/usr/bin/env python3
"""board-read — read a saved .kicad_pcb, headless, and print Markdown tables.

    board-read.py <board.kicad_pcb> footprints [--ref PAT ...] [--box X0 Y0 X1 Y1]
    board-read.py <board.kicad_pcb> pads <ref>
    board-read.py <board.kicad_pcb> nets [PAT ...]
    board-read.py <board.kicad_pcb> zones [--net PAT ...] [--name PAT ...]
    board-read.py <board.kicad_pcb> rule-areas [--name PAT ...]
    board-read.py <board.kicad_pcb> tracks [--net PAT ...] [--box X0 Y0 X1 Y1] [--layer L]

Read-only: the file is never written. It reads the saved file, not what
an open editor holds unsaved. Patterns are shell wildcards, `fnmatch`,
case-sensitive. Millimetres, KiCad's axes, y down, degrees.

This file is also the library of the four writing skills — place-relative,
route-pad, copper-transform, rule-area-copy — loaded by sibling path,
board-build-tool.md §5.3. The library holds:

- the KiCad guard: a writing tool refuses while any KiCad process runs
- the harness: back-up, a worker in KiCad's Python through pcb-script, the
  safe save, verification by kicad-cli DRC, the atomic replace, the
  summary table
- the pcbnew helpers: units, layers, footprints, pads, net classes,
  selection, tables

The safe save. `pcbnew.SaveBoard` segfaults outside KiCad in
`SETTINGS_MANAGER::SaveProjectAs` (radar_2 crash-incidents 1.6): it saves
the project file too, through a settings manager that does not exist
outside KiCad. The harness never calls it. The worker saves the board
alone, through the s-expression plugin,
`PCB_IO_MGR.FindPlugin(KICAD_SEXP).SaveBoard(path, board)`, to a
temporary file in its own subprocess. The real board is replaced only
after the worker exited 0, the file is whole and kicad-cli loads it and
runs DRC on it. The worker leaves by `os._exit` (pcb-script), so the wx
teardown never runs. The worker never runs pcbnew's DRC or zone filler:
their thread pools crash outside KiCad.
"""

import argparse
import datetime
import fnmatch
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PCB_SCRIPT = ROOT / "skills" / "pcb-script" / "pcb-script"
KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
NOISE = "create wxApp before calling this"   # pcbnew's harmless assert line
SCRATCH = ("/private/tmp/", "/tmp/", "/private/var/folders/")
ENV_RESULT = "BOARD_BUILD_RESULT"
ENV_OUT = "BOARD_BUILD_OUT"
ENV_READ = "BOARD_BUILD_READ"
NM = 1_000_000


class Bad(SystemExit):
    def __init__(self, tool, message):
        super().__init__(f"{tool}: {message}")


# ---------------------------------------------------------------- tables

def table(header, rows, title=None):
    """A Markdown table, each row numbered in a leading `#` column."""
    out = []
    if title:
        out += [f"**{title}**", ""]
    out.append("| # | " + " | ".join(header) + " |")
    out.append("|---" * (len(header) + 1) + "|")
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | " + " | ".join(cell(c) for c in r) + " |")
    if not rows:
        out.append("| — | " + " | ".join("" for _ in header) + " |")
    return "\n".join(out)


def cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return fmt(v)
    return str(v).replace("|", "\\|").replace("\n", " ")


def fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ---------------------------------------------------------------- pcbnew

def pcb():
    import pcbnew
    return pcbnew


def mm(v):
    return v / NM


def iu(v):
    return int(round(float(v) * NM))


def xy(p):
    return f"{fmt(mm(p.x))}, {fmt(mm(p.y))}"


def vec(x, y):
    return pcb().VECTOR2I(iu(x), iu(y))


def angle(deg):
    p = pcb()
    return p.EDA_ANGLE(float(deg), p.DEGREES_T)


def norm_deg(d):
    d = math.fmod(d, 360.0)
    if d <= -180.0:
        d += 360.0
    if d > 180.0:
        d -= 360.0
    return round(d, 4) + 0.0


def layers(b):
    """Name to id, for the board's own names and KiCad's standard ones."""
    p = pcb()
    out = {}
    for i in range(p.PCB_LAYER_ID_COUNT):
        try:
            out[b.GetStandardLayerName(i)] = i
            out[b.GetLayerName(i)] = i
        except Exception:
            pass
    return out


def layer_id(tool, b, name):
    ids = layers(b)
    if name not in ids:
        raise Bad(tool, f"no layer {name!r} on the board")
    return ids[name]


def layer_names(b, lset):
    return " ".join(b.GetLayerName(i) for i in lset.Seq())


def footprint(tool, b, ref):
    hits = [f for f in b.GetFootprints() if f.GetReference() == ref]
    if not hits:
        raise Bad(tool, f"no footprint {ref} on the board")
    if len(hits) > 1:
        raise Bad(tool, f"{len(hits)} footprints carry {ref}")
    return hits[0]


def pad(tool, b, spec):
    """`REF.PAD`, the pad numbered PAD of footprint REF. Refused when the
    number is missing or held by more than one pad. `REF.PAD@X,Y` picks,
    among the pads numbered PAD, the one whose centre is nearest (X, Y) mm;
    refused when two are equally near."""
    near = None
    if "@" in spec:
        spec, at = spec.split("@", 1)
        try:
            near = tuple(float(v) for v in at.split(","))
        except ValueError:
            near = ()
        if len(near) != 2:
            raise Bad(tool, f"{at!r} is not X,Y")
    if "." not in spec:
        raise Bad(tool, f"{spec!r} is not REF.PAD")
    ref, num = spec.rsplit(".", 1)
    fp = footprint(tool, b, ref)
    hits = [q for q in fp.Pads() if q.GetNumber() == num]
    if not hits:
        raise Bad(tool, f"{ref} has no pad {num}")
    if len(hits) > 1 and near is None:
        raise Bad(tool, f"{ref} has {len(hits)} pads numbered {num}; "
                  f"give {ref}.{num}@X,Y")
    if near is not None:
        def d(q):
            return round(math.hypot(mm(q.GetPosition().x) - near[0],
                                    mm(q.GetPosition().y) - near[1]), 6)
        hits.sort(key=d)
        if len(hits) > 1 and d(hits[0]) == d(hits[1]):
            raise Bad(tool, f"two pads {ref}.{num} lie equally near "
                      f"{near[0]}, {near[1]}")
    return hits[0]


def match(name, pats):
    return any(fnmatch.fnmatchcase(name, p) for p in pats)


def in_box(box, *pts):
    x0, y0, x1, y1 = box
    return all(x0 <= mm(q.x) <= x1 and y0 <= mm(q.y) <= y1 for q in pts)


def bbox_in(box, bb):
    x0, y0, x1, y1 = box
    return (x0 <= mm(bb.GetLeft()) and mm(bb.GetRight()) <= x1
            and y0 <= mm(bb.GetTop()) and mm(bb.GetBottom()) <= y1)


def box_arg(v):
    x0, y0, x1, y1 = (float(q) for q in v)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def bbox_text(bb):
    return (f"{fmt(mm(bb.GetLeft()))}, {fmt(mm(bb.GetTop()))} — "
            f"{fmt(mm(bb.GetRight()))}, {fmt(mm(bb.GetBottom()))}")


def is_via(t):
    return t.GetClass() == "PCB_VIA"


def is_arc(t):
    return t.GetClass() == "PCB_ARC"


def kind(item):
    c = item.GetClass()
    if c == "FOOTPRINT":
        return "footprint"
    if c == "PCB_VIA":
        return "via"
    if c in ("PCB_TRACK", "PCB_ARC"):
        return "track"
    if c == "ZONE":
        return "rule-area" if item.GetIsRuleArea() else "zone"
    return c


def describe(b, item):
    """One short name for an item in a change table."""
    k = kind(item)
    if k == "footprint":
        return item.GetReference()
    if k == "via":
        return f"via {item.GetNetname()} @ {xy(item.GetPosition())}"
    if k == "track":
        return (f"track {item.GetNetname()} {b.GetLayerName(item.GetLayer())} "
                f"{xy(item.GetStart())} → {xy(item.GetEnd())}")
    name = item.GetZoneName() or "(no name)"
    if k == "zone":
        return f"zone {name} {item.GetNetname()}"
    return f"rule area {name}"


def place_of(item):
    """Where an item sits, for before / after columns."""
    k = kind(item)
    if k == "footprint":
        return (f"{xy(item.GetPosition())} @ "
                f"{fmt(norm_deg(item.GetOrientationDegrees()))}°")
    if k == "via":
        return xy(item.GetPosition())
    if k == "track":
        return f"{xy(item.GetStart())} → {xy(item.GetEnd())}"
    return bbox_text(item.GetBoundingBox())


def netclass_width(b, board_path, net):
    """The track width of the net's class, from the project file beside the
    board: explicit assignments, then patterns, then Default."""
    pro = Path(board_path).with_suffix(".kicad_pro")
    try:
        ns = json.loads(pro.read_text())["net_settings"]
    except Exception:
        ns = {}
    classes = {c["name"]: c for c in ns.get("classes", [])}
    cls = None
    assigned = ns.get("netclass_assignments") or {}
    if net in assigned:
        a = assigned[net]
        cls = a[0] if isinstance(a, list) and a else a
    if cls is None:
        for pat in ns.get("netclass_patterns") or []:
            if fnmatch.fnmatchcase(net, pat.get("pattern", "")):
                cls = pat.get("netclass")
                break
    cls = cls if cls in classes else "Default"
    w = classes.get(cls, {}).get("track_width")
    if w is None:
        w = classes.get("Default", {}).get("track_width")
    if w is None:
        w = mm(b.GetDesignSettings().GetCurrentTrackWidth())
    return cls, float(w)


def local_clearance(z):
    v = z.GetLocalClearance()
    if v is None:
        return None
    try:
        return mm(int(v))
    except Exception:
        return None


# ---------------------------------------------------------------- guard

def kicad_running():
    run = subprocess.run(["pgrep", "-il", "kicad"], capture_output=True,
                         text=True)
    return [line for line in run.stdout.splitlines() if line.strip()]


def guard(tool, board):
    """Refuse while KiCad runs: it holds the board in memory and writes it
    back over a change made underneath it.

    The one exception is for testing the tools: BOARD_BUILD_SCRATCH=1 and
    a board under a temporary directory that KiCad has not opened (no
    `~<name>.kicad_pcb.lck` beside it). A project board never sits there."""
    procs = kicad_running()
    if not procs:
        return
    real = str(Path(board).resolve())
    lock = Path(board).with_name(f"~{Path(board).name}.lck")
    if (os.environ.get("BOARD_BUILD_SCRATCH") == "1"
            and real.startswith(SCRATCH) and not lock.exists()):
        print(f"{tool}: KiCad is running; scratch board under a temporary "
              f"directory, BOARD_BUILD_SCRATCH=1: allowed for a test")
        return
    raise Bad(tool, "KiCad is running ("
              + "; ".join(procs) + "). Save all in KiCad and quit it, "
              "confirm no kicad process remains, then run again. "
              "The board is unchanged.")


# ---------------------------------------------------------------- harness

def board_arg(tool, path):
    p = Path(path).expanduser()
    if p.suffix != ".kicad_pcb":
        raise Bad(tool, f"{p} is not a .kicad_pcb")
    if not p.is_file():
        raise Bad(tool, f"no board at {p}")
    return p.resolve()


def worker_call(tool, script, argv, env_extra):
    env = dict(os.environ, **env_extra)
    run = subprocess.run([str(PCB_SCRIPT), str(script), *argv], env=env,
                         capture_output=True, text=True)
    err = "\n".join(line for line in run.stderr.splitlines()
                    if NOISE not in line).strip()
    if run.stdout.strip():
        print(run.stdout.rstrip())
    if run.returncode < 0:
        raise Bad(tool, f"KiCad's Python died on signal {-run.returncode}; "
                  f"the board is unchanged. {err[-400:]}")
    if run.returncode != 0:
        raise SystemExit(err or f"{tool}: worker failed, exit "
                         f"{run.returncode}; the board is unchanged")
    return err


def drc(tool, pcb_path, report):
    run = subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json",
                          "-o", str(report), str(pcb_path)],
                         capture_output=True, text=True)
    if run.returncode != 0 or not Path(report).is_file():
        raise Bad(tool, f"kicad-cli could not check {pcb_path.name}: "
                  + (run.stderr or run.stdout).strip()[-400:]
                  + "; the board is unchanged")
    d = json.loads(Path(report).read_text())
    count = {}
    for v in d.get("violations", []):
        count[v["type"]] = count.get(v["type"], 0) + 1
    count["unconnected_items"] = len(d.get("unconnected_items", []))
    return count


def whole(path):
    """A saved board ends in its closing paren."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 64))
            tail = f.read().rstrip()
        return path.stat().st_size > 0 and tail.endswith(b")")
    except OSError:
        return False


def stage_copy(src, pro, dru, folder, stem):
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{stem}.kicad_pcb"
    if src is not None:
        shutil.copy2(src, dst)
    if pro.is_file():
        shutil.copy2(pro, folder / f"{stem}.kicad_pro")
    if dru.is_file():
        shutil.copy2(dru, folder / f"{stem}.kicad_dru")
    return dst


def run_edit(tool, script, parser, edit):
    """The writing tools' main.

    Outer process, any Python 3: guard, back-up, the worker, verification,
    the atomic replace, the tables. Worker process, KiCad's Python under
    pcb-script: load, `edit(board, args)`, the safe save to the temporary
    file. `edit` returns {"header": [...], "rows": [[...]], "notes": [...]}
    and raises Bad to refuse."""
    a = parser.parse_args()
    if os.environ.get(ENV_RESULT):
        _worker(tool, a, edit)
        return
    board = board_arg(tool, a.board)
    guard(tool, board)
    stem = board.stem
    pro = board.with_suffix(".kicad_pro")
    dru = board.with_suffix(".kicad_dru")
    tmp = board.parent / f".{stem}.board-edit-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        before = board.stat()
        result = tmp / "result.json"
        out = tmp / "after" / f"{stem}.kicad_pcb"
        out.parent.mkdir()
        worker_call(tool, script, sys.argv[1:],
                    {ENV_RESULT: str(result),
                     ENV_OUT: "" if a.dry_run else str(out)})
        res = json.loads(result.read_text())
        print(table(res["header"], res["rows"],
                    "Dry run — would change" if a.dry_run else "Changed"))
        for n in res.get("notes", []):
            print(f"- {n}")
        if a.dry_run:
            print(f"{tool}: dry run, {len(res['rows'])} changes listed; "
                  f"nothing written")
            return
        if not res["rows"]:
            print(f"{tool}: nothing to change; the board is unchanged")
            return
        # the back-up, before anything is written to the board
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = board.with_name(f"{board.name}.bak-{stamp}")
        shutil.copy2(board, bak)
        if not whole(out):
            raise Bad(tool, "the saved temporary board is missing or cut "
                      "short; the board is unchanged")
        ref = stage_copy(board, pro, dru, tmp / "before", stem)
        stage_copy(None, pro, dru, tmp / "after", stem)
        d0 = drc(tool, ref, tmp / "drc-before.json")
        d1 = drc(tool, out, tmp / "drc-after.json")
        now = board.stat()
        if (now.st_mtime_ns, now.st_size) != (before.st_mtime_ns,
                                              before.st_size):
            raise Bad(tool, "the board changed on disk during the run; "
                      "nothing replaced")
        os.replace(out, board)
        keys = sorted(set(d0) | set(d1))
        rows = [[k, d0.get(k, 0), d1.get(k, 0), d1.get(k, 0) - d0.get(k, 0)]
                for k in keys if d0.get(k, 0) != d1.get(k, 0)]
        rows.append(["total", sum(d0.values()), sum(d1.values()),
                     sum(d1.values()) - sum(d0.values())])
        print()
        print(table(["DRC", "before", "after", "change"], rows,
                    "DRC, kicad-cli, zones not refilled"))
        print(f"{tool}: {len(res['rows'])} changes written to {board.name}; "
              f"back-up {bak.name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _worker(tool, a, edit):
    p = pcb()
    board = str(Path(a.board).expanduser().resolve())
    b = p.LoadBoard(board)
    if b is None:
        raise Bad(tool, f"pcbnew could not load {board}")
    res = edit(b, a)
    res.setdefault("notes", [])
    out = os.environ.get(ENV_OUT)
    if out and res["rows"]:
        io = p.PCB_IO_MGR.FindPlugin(p.PCB_IO_MGR.KICAD_SEXP)
        io.SaveBoard(out, b)
    Path(os.environ[ENV_RESULT]).write_text(json.dumps(res))


def edit_parser(prog, doc):
    ap = argparse.ArgumentParser(prog=prog, description=doc,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", help="the saved .kicad_pcb")
    ap.add_argument("--dry-run", action="store_true",
                    help="list the changes, write nothing")
    return ap


# ---------------------------------------------------------------- reads

def q_footprints(b, a):
    rows = []
    for f in sorted(b.GetFootprints(), key=lambda f: f.GetReference()):
        if a.ref and not match(f.GetReference(), a.ref):
            continue
        if a.box and not in_box(a.box, f.GetPosition()):
            continue
        rows.append([f.GetReference(), f.GetValue(), f.GetFPIDAsString(),
                     mm(f.GetPosition().x), mm(f.GetPosition().y),
                     norm_deg(f.GetOrientationDegrees()),
                     b.GetLayerName(f.GetLayer())])
    return table(["ref", "value", "footprint", "x", "y", "rotation", "layer"],
                 rows)


def q_pads(b, a):
    fp = footprint("board-read", b, a.ref)
    rows = []
    for q in fp.Pads():
        s = q.GetSize(q.GetPrincipalLayer())
        rows.append([q.GetNumber(), q.GetNetname(), mm(q.GetPosition().x),
                     mm(q.GetPosition().y), f"{fmt(mm(s.x))} × {fmt(mm(s.y))}",
                     norm_deg(q.GetOrientation().AsDegrees()),
                     layer_names(b, q.GetLayerSet())])
    rows.sort(key=lambda r: (len(r[0]), r[0]))
    return table(["pad", "net", "x", "y", "size", "rotation", "layers"], rows)


def q_nets(b, a):
    pads = {}
    for f in b.GetFootprints():
        for q in f.Pads():
            n = q.GetNetname()
            if n:
                pads.setdefault(n, []).append((f.GetReference(), q))
    if not a.pats:
        rows = [[n, len(v)] for n, v in sorted(pads.items())]
        return table(["net", "pads"], rows)
    rows = []
    for n in sorted(pads):
        if not match(n, a.pats):
            continue
        for ref, q in sorted(pads[n], key=lambda t: (t[0], t[1].GetNumber())):
            rows.append([n, f"{ref}.{q.GetNumber()}", mm(q.GetPosition().x),
                         mm(q.GetPosition().y)])
    return table(["net", "pad", "x", "y"], rows)


FILL = {0: "solid", 1: "hatch"}
CONN = {0: "none", 1: "thermal", 2: "solid", 3: "THT thermal", -1: "inherited"}
ISLAND = {0: "always remove", 1: "never remove", 2: "below area"}


def q_zones(b, a):
    rows = []
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        if a.net and not match(z.GetNetname(), a.net):
            continue
        if a.name and not match(z.GetZoneName(), a.name):
            continue
        fill = (f"{FILL.get(z.GetFillMode(), z.GetFillMode())}; pads "
                f"{CONN.get(z.GetPadConnection(), z.GetPadConnection())}; "
                f"thermal gap {fmt(mm(z.GetThermalReliefGap()))}; spoke "
                f"{fmt(mm(z.GetThermalReliefSpokeWidth()))}; islands "
                f"{ISLAND.get(z.GetIslandRemovalMode(), z.GetIslandRemovalMode())}; "
                f"{'filled' if z.IsFilled() else 'not filled'}")
        rows.append([z.GetZoneName(), z.GetNetname(),
                     layer_names(b, z.GetLayerSet()), local_clearance(z),
                     mm(z.GetMinThickness()), fill, z.GetAssignedPriority(),
                     bbox_text(z.GetBoundingBox())])
    return table(["name", "net", "layers", "clearance", "min width", "fill",
                  "priority", "bbox"], rows)


def keepouts(z):
    k = [("tracks", z.GetDoNotAllowTracks()), ("vias", z.GetDoNotAllowVias()),
         ("pads", z.GetDoNotAllowPads()),
         ("footprints", z.GetDoNotAllowFootprints()),
         ("zone fills", z.GetDoNotAllowZoneFills())]
    return "no " + ", no ".join(n for n, v in k if v) if any(
        v for _, v in k) else "none"


def q_rule_areas(b, a):
    rows = []
    for z in b.Zones():
        if not z.GetIsRuleArea():
            continue
        if a.name and not match(z.GetZoneName(), a.name):
            continue
        rows.append([z.GetZoneName(), layer_names(b, z.GetLayerSet()),
                     keepouts(z), z.GetAssignedPriority(),
                     bbox_text(z.GetBoundingBox()), z.m_Uuid.AsString()[:8]])
    return table(["name", "layers", "keepouts", "priority", "bbox", "uuid"],
                 rows)


def q_tracks(b, a):
    lid = layer_id("board-read", b, a.layer) if a.layer else None
    rows = []
    for t in b.GetTracks():
        if a.net and not match(t.GetNetname(), a.net):
            continue
        if is_via(t):
            if lid is not None and not t.IsOnLayer(lid):
                continue
            if a.box and not in_box(a.box, t.GetPosition()):
                continue
            rows.append(["via", t.GetNetname(),
                         f"{b.GetLayerName(t.TopLayer())}–"
                         f"{b.GetLayerName(t.BottomLayer())}",
                         xy(t.GetPosition()), "",
                         f"⌀ {fmt(mm(t.GetWidth(t.TopLayer())))}, drill "
                         f"{fmt(mm(t.GetDrill()))}"])
            continue
        if lid is not None and t.GetLayer() != lid:
            continue
        if a.box and not (in_box(a.box, t.GetStart())
                          or in_box(a.box, t.GetEnd())):
            continue
        rows.append(["arc" if is_arc(t) else "track", t.GetNetname(),
                     b.GetLayerName(t.GetLayer()), xy(t.GetStart()),
                     xy(t.GetEnd()), fmt(mm(t.GetWidth()))])
    rows.sort(key=lambda r: (r[1], r[0], r[2], r[3]))
    return table(["kind", "net", "layer", "start / at", "end", "width"], rows)


def read_main():
    ap = argparse.ArgumentParser(prog="board-read")
    ap.add_argument("board")
    sub = ap.add_subparsers(dest="what", required=True)
    s = sub.add_parser("footprints")
    s.add_argument("--ref", nargs="+")
    s.add_argument("--box", nargs=4, type=float)
    s = sub.add_parser("pads")
    s.add_argument("ref")
    s = sub.add_parser("nets")
    s.add_argument("pats", nargs="*")
    s = sub.add_parser("zones")
    s.add_argument("--net", nargs="+")
    s.add_argument("--name", nargs="+")
    s = sub.add_parser("rule-areas")
    s.add_argument("--name", nargs="+")
    s = sub.add_parser("tracks")
    s.add_argument("--net", nargs="+")
    s.add_argument("--box", nargs=4, type=float)
    s.add_argument("--layer")
    a = ap.parse_args()
    if getattr(a, "box", None):
        a.box = box_arg(a.box)
    board = board_arg("board-read", a.board)
    if not os.environ.get(ENV_READ):
        worker_call("board-read", Path(__file__).resolve(), sys.argv[1:],
                    {ENV_READ: "1"})
        return
    b = pcb().LoadBoard(str(board))
    if b is None:
        raise Bad("board-read", f"pcbnew could not load {board}")
    fn = {"footprints": q_footprints, "pads": q_pads, "nets": q_nets,
          "zones": q_zones, "rule-areas": q_rule_areas,
          "tracks": q_tracks}[a.what]
    print(fn(b, a))


def load(name):
    """A sibling skill's script as a module: <root>/skills/<name>/<name>.py."""
    import importlib.util
    path = ROOT / "skills" / name / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    read_main()
