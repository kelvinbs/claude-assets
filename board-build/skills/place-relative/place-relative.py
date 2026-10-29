#!/usr/bin/env python3
"""place-relative — place a footprint by one of its pads, or move a set of
footprints rigidly, on a saved .kicad_pcb, headless.

    place-relative.py <board> <ref> --pad P --to REF.PAD [--offset DX DY]
                      [--face R|L|U|D | --rot DEG] [--dry-run]
    place-relative.py <board> --refs REF ... --move DX DY [--dry-run]
    place-relative.py <board> --refs REF ... --rotate DEG --about X Y [--dry-run]

Pad mode: footprint <ref> is turned, then moved so its pad P sits at pad
REF.PAD's centre plus (DX, DY) mm. `--face` turns it so pad P faces that
way from the footprint's origin: R +x, L -x, U -y (up on screen), D +y.
`--rot` sets the rotation outright. Neither keeps the rotation it has.

Rigid mode: every named footprint moves by (DX, DY), or turns DEG
counter-clockwise on screen about (X, Y), as one body. Only footprints;
tracks and zones are copper-transform's.

KiCad must not be running. The board is backed up, saved to a temporary
file in a worker, checked by kicad-cli and then replaced — board-read.py.
"""

import math
import sys
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "place-relative"
FACE = {"R": 0.0, "U": 90.0, "L": 180.0, "D": 270.0}   # screen angle, CCW


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("ref", nargs="?", help="the footprint to place")
    ap.add_argument("--pad", help="its pad number")
    ap.add_argument("--to", help="REF.PAD of the pad it is placed from")
    ap.add_argument("--offset", nargs=2, type=float, default=[0.0, 0.0],
                    metavar=("DX", "DY"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--face", choices=sorted(FACE))
    g.add_argument("--rot", type=float)
    ap.add_argument("--refs", nargs="+")
    ap.add_argument("--move", nargs=2, type=float, metavar=("DX", "DY"))
    ap.add_argument("--rotate", type=float, metavar="DEG")
    ap.add_argument("--about", nargs=2, type=float, metavar=("X", "Y"))
    return ap


def screen_angle(v):
    """The direction of a board vector as a screen angle, CCW, y down."""
    return math.degrees(math.atan2(-v.y, v.x))


def pad_vec(fp, q):
    return q.GetPosition() - fp.GetPosition()


def check(a):
    pad_mode = a.ref is not None
    rigid = a.refs is not None
    if pad_mode == rigid:
        raise br.Bad(TOOL, "give either <ref> --pad --to, or --refs")
    if pad_mode and not (a.pad and a.to):
        raise br.Bad(TOOL, "pad mode needs --pad and --to")
    if rigid and (a.move is None) == (a.rotate is None):
        raise br.Bad(TOOL, "--refs needs exactly one of --move, --rotate")
    if rigid and a.rotate is not None and a.about is None:
        raise br.Bad(TOOL, "--rotate needs --about X Y")
    return pad_mode


def edit(b, a):
    pad_mode = check(a)
    rows, notes = [], []
    if pad_mode:
        fp = br.footprint(TOOL, b, a.ref)
        mine = br.pad(TOOL, b, f"{a.ref}.{a.pad}")
        other = br.pad(TOOL, b, a.to)
        if other.GetParentFootprint().GetReference() == a.ref:
            raise br.Bad(TOOL, f"{a.to} is on {a.ref} itself")
        was = br.place_of(fp)
        if a.rot is not None:
            fp.SetOrientationDegrees(br.norm_deg(a.rot))
        elif a.face:
            v = pad_vec(fp, mine)
            if math.hypot(v.x, v.y) < 1000:
                raise br.Bad(TOOL, f"pad {a.pad} sits on {a.ref}'s origin; it "
                             f"faces no way. Give --rot")
            want = FACE[a.face]
            for _ in range(3):   # measured, not assumed: the back side mirrors
                err = br.norm_deg(want - screen_angle(pad_vec(fp, mine)))
                if abs(err) < 1e-3:
                    break
                sign = -1.0 if fp.IsFlipped() else 1.0
                fp.SetOrientationDegrees(
                    br.norm_deg(fp.GetOrientationDegrees() + sign * err))
            err = br.norm_deg(want - screen_angle(pad_vec(fp, mine)))
            if abs(err) > 0.01:
                raise br.Bad(TOOL, f"could not turn pad {a.pad} to face "
                             f"{a.face}: off by {err:.3f}°")
        target = other.GetPosition() + br.vec(*a.offset)
        fp.SetPosition(fp.GetPosition() + (target - mine.GetPosition()))
        got = mine.GetPosition()
        if (got.x, got.y) != (target.x, target.y):
            raise br.Bad(TOOL, f"pad landed at {br.xy(got)}, not "
                         f"{br.xy(target)}")
        rows.append([a.ref, was, br.place_of(fp)])
        notes.append(f"tracks on {a.ref} were not moved")
        notes.append(f"{a.ref}.{a.pad} at {br.xy(got)}: {a.to} "
                     f"{br.xy(other.GetPosition())} + ({br.fmt(a.offset[0])}, "
                     f"{br.fmt(a.offset[1])})")
    else:
        fps = [br.footprint(TOOL, b, r) for r in dict.fromkeys(a.refs)]
        for fp in fps:
            was = br.place_of(fp)
            if a.move is not None:
                fp.Move(br.vec(*a.move))
            else:
                fp.Rotate(br.vec(*a.about), br.angle(a.rotate))
            rows.append([fp.GetReference(), was, br.place_of(fp)])
        notes.append("tracks, vias and zones were not moved; "
                     "copper-transform moves them")
    return {"header": ["footprint", "before (x, y @ rotation)", "after"],
            "rows": rows, "notes": notes}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
