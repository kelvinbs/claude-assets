#!/usr/bin/env python3
"""item-delete — remove tracks, vias, zones or rule areas from a saved
.kicad_pcb, headless. Footprints are never removed: they follow the record.

    item-delete.py <board> [--net PAT ...] [--name PAT ...] [--uuid PREFIX ...]
                   [--box X0 Y0 X1 Y1] [--layer L] --kind K ... [--dry-run]

Selection. `--kind` is required: track, via, zone, rule-area. Each
criterion applies to the kinds it can name:

- `--net`: tracks, vias and zones, by net name
- `--name`: zones and rule areas, by name
- `--uuid`: zones and rule areas, by the uuid prefix board-read prints
- `--box`: every kind, the item's bounding box wholly inside
- `--layer`: tracks, on that layer

An item is selected when every criterion that applies to its kind matches,
and at least one does. Patterns are shell wildcards. Nothing selected is
refused.

Run `--dry-run` first: it lists every selected item. KiCad must not be
running. The board is backed up, saved to a temporary file in a worker,
checked by kicad-cli and then replaced — board-read.py.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "item-delete"
KINDS = ["track", "via", "zone", "rule-area"]
APPLIES = {"net": {"track", "via", "zone"}, "name": {"zone", "rule-area"},
           "uuid": {"zone", "rule-area"}, "box": set(KINDS),
           "layer": {"track"}}


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("--net", nargs="+")
    ap.add_argument("--name", nargs="+")
    ap.add_argument("--uuid", nargs="+")
    ap.add_argument("--box", nargs=4, type=float)
    ap.add_argument("--layer")
    ap.add_argument("--kind", nargs="+", choices=KINDS, required=True)
    return ap


def selected(a, lid, item):
    k = br.kind(item)
    if k not in a.kind:
        return False
    tests = []
    if a.net and k in APPLIES["net"]:
        tests.append(br.match(item.GetNetname(), a.net))
    if a.name and k in APPLIES["name"]:
        tests.append(br.match(item.GetZoneName(), a.name))
    if a.uuid and k in APPLIES["uuid"]:
        u = item.m_Uuid.AsString()
        tests.append(any(u.startswith(q) for q in a.uuid))
    if a.box and k in APPLIES["box"]:
        tests.append(br.bbox_in(a.box, item.GetBoundingBox()))
    if lid is not None and k in APPLIES["layer"]:
        tests.append(item.GetLayer() == lid)
    return bool(tests) and all(tests)


def edit(b, a):
    if not (a.net or a.name or a.uuid or a.box or a.layer):
        raise br.Bad(TOOL, "no selection: give --net, --name, --uuid, --box "
                     "or --layer")
    if a.box:
        a.box = br.box_arg(a.box)
    lid = br.layer_id(TOOL, b, a.layer) if a.layer else None
    items = [t for t in b.GetTracks()] + [z for z in b.Zones()]
    sel = [i for i in items if selected(a, lid, i)]
    if not sel:
        raise br.Bad(TOOL, "the selection holds nothing")
    rows = []
    for i in sel:
        k = br.kind(i)
        rows.append(["delete", k, br.describe(b, i), br.place_of(i),
                     i.GetNetname() if k != "rule-area" else ""])
        b.Delete(i)
    count = {}
    for r in rows:
        count[r[1]] = count.get(r[1], 0) + 1
    return {"header": ["change", "kind", "item", "where", "net"],
            "rows": rows,
            "notes": ["selected: " + "; ".join(f"{v} {k}" for k, v in
                                              sorted(count.items()))]}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
