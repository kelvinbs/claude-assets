#!/usr/bin/env python3
"""zone-net-set — put copper zones on another net, on a saved .kicad_pcb,
headless. Only zones change: tracks, vias, pads and rule areas never.

    zone-net-set.py <board> --to NET [--net PAT ...] [--name PAT ...]
                    [--uuid PREFIX ...] [--box X0 Y0 X1 Y1] [--layer L]
                    [--dry-run]

Selection, copper zones only, every criterion given must match and one at
least must be given: `--net` the zone's present net, `--name` its name,
`--uuid` the prefix board-read prints, `--box` its bounding box wholly
inside, `--layer` a layer it is on. Patterns are shell wildcards. Nothing
selected, or a NET the board does not hold, is refused.

Run `--dry-run` first. KiCad must not be running. The board is backed up,
saved to a temporary file in a worker, checked by kicad-cli and then
replaced — board-read.py. Fills are not recomputed: zone-fill.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "zone-net-set"


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("--to", required=True, metavar="NET")
    ap.add_argument("--net", nargs="+")
    ap.add_argument("--name", nargs="+")
    ap.add_argument("--uuid", nargs="+")
    ap.add_argument("--box", nargs=4, type=float)
    ap.add_argument("--layer")
    return ap


def edit(b, a):
    if not (a.net or a.name or a.uuid or a.box or a.layer):
        raise br.Bad(TOOL, "no selection: give --net, --name, --uuid, --box "
                     "or --layer")
    net = b.FindNet(a.to)
    if net is None or not a.to:
        raise br.Bad(TOOL, f"no net {a.to!r} on the board")
    box = br.box_arg(a.box) if a.box else None
    lid = br.layer_id(TOOL, b, a.layer) if a.layer else None
    rows = []
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        if a.net and not br.match(z.GetNetname(), a.net):
            continue
        if a.name and not br.match(z.GetZoneName(), a.name):
            continue
        if a.uuid and not any(z.m_Uuid.AsString().startswith(q)
                              for q in a.uuid):
            continue
        if box and not br.bbox_in(box, z.GetBoundingBox()):
            continue
        if lid is not None and not z.IsOnLayer(lid):
            continue
        was = z.GetNetname()
        if was == a.to:
            continue
        z.SetNet(net)
        rows.append([br.describe(b, z), br.layer_names(b, z.GetLayerSet()),
                     br.bbox_text(z.GetBoundingBox()), was, a.to])
    if not rows:
        raise br.Bad(TOOL, "no zone selected, or every one already on "
                     + a.to)
    return {"header": ["zone", "layers", "bbox", "net before", "net after"],
            "rows": rows,
            "notes": ["fills are not recomputed; zone-fill"]}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
