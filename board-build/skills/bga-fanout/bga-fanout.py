#!/usr/bin/env python3
"""bga-fanout — fan out a ball grid array on the board open in KiCad, in steps.

    bga-fanout.py <board-dir> <ref> groups
    bga-fanout.py <board-dir> <ref> top    --group NAME [--save] [--dry-run]
    bga-fanout.py <board-dir> <ref> vias   [--save] [--dry-run]
    bga-fanout.py <board-dir> <ref> straps [--save] [--dry-run]
    bga-fanout.py <board-dir> <ref> route  --group NAME [--save] [--dry-run]

groups  each signal net, the parts at its other end, the side each part sits on.
        Claude groups the nets and writes <board-dir>/fanout/<ref>.json.
top     the group's signals on F.Cu from their pads, no ball via, as many as
        F.Cu holds. The rest are reported.
vias    a via in every connected ball not already routed from its pad.
straps  adjacent power balls of one net joined on F.Cu, widened up to 0.5 mm.
route   the group's remaining signals from their ball vias, on F.Cu or In2.Cu.

Every trace heads only in its group's direction and ends in a via outside the
courtyard, nearer costing less; a GND via 0.40 mm from it toward the part.
Differential pairs share a layer, run adjacent, and end side by side.
A run touches only its own group. One read, one remove, one create.
"""

import argparse
import heapq
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
PY = VENV / "bin" / "python"
NM = 1e6
G = 0.1                      # mm, routing grid
DIRS = {'N': (0, -1), 'S': (0, 1), 'E': (1, 0), 'W': (-1, 0),
        'NE': (1, -1), 'NW': (-1, -1), 'SE': (1, 1), 'SW': (-1, 1)}
D8 = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
KICAD = 'first process whose bundle identifier is "org.kicad.kicad"'
SCH = 'first window whose name contains "Schematic Editor"'


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"bga-fanout: {message}")


def ensure_kipy():
    """Run under the tool's venv, with kicad-python in it."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not PY.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True, capture_output=True)
    if subprocess.run([str(PY), "-c", "import kipy"], capture_output=True).returncode != 0:
        run = subprocess.run([str(VENV / "bin" / "pip"), "install", "-q", "kicad-python"],
                             capture_output=True, text=True)
        if run.returncode != 0:
            raise Bad("could not install kicad-python: " + (run.stderr or run.stdout).strip()[-200:])
    os.execv(str(PY), [str(PY), __file__, *sys.argv[1:]])


def retry_busy():
    """KiCad answers busy while it redraws; retry each call up to 4 min."""
    import kipy.client as c
    send = c.KiCadClient.send

    def patient(self, cmd, rt):
        for _ in range(120):
            try:
                return send(self, cmd, rt)
            except Exception as e:
                if "busy" not in str(e):
                    raise
                time.sleep(2)
        return send(self, cmd, rt)
    c.KiCadClient.send = patient


def osa(*lines):
    """Run AppleScript lines through System Events on KiCad's process."""
    args = []
    for line in ('tell application "System Events" to tell ' + KICAD, *lines, 'end tell'):
        args += ["-e", line]
    run = subprocess.run(["osascript", *args], capture_output=True, text=True)
    return run.returncode, run.stdout.strip()


def sch_open():
    """The Schematic Editor's window is on screen. A lock file is no proof: a crash leaves it."""
    code, out = osa(f'  return exists ({SCH})')
    if code != 0:
        raise Bad("cannot read KiCad's windows through System Events")
    return out == "true"


def close_schematic():
    """Save and close the Schematic Editor: raise it, Cmd+S only once it is in front,
    then its own close button, never Cmd+W. Waits up to 15 s."""
    osa('  set frontmost to true',
        f'  perform action "AXRaise" of ({SCH})',
        '  delay 0.5',
        '  if name of front window contains "Schematic Editor" then',
        '    keystroke "s" using command down',
        '    delay 2',
        f'    click (first button of ({SCH}) whose subrole is "AXCloseButton")',
        '  end if')
    for _ in range(30):
        if not sch_open():
            return
        time.sleep(0.5)


def pd(px, py, ax, ay, bx, by):
    """Point to segment distance."""
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    u = 0 if ll == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - ax - u * dx, py - ay - u * dy)


def segd(p, q, r, s):
    """Segment to segment distance."""
    o = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if o(p, q, r) * o(p, q, s) < 0 and o(r, s, p) * o(r, s, q) < 0:
        return 0
    return min(pd(*p, *r, *s), pd(*q, *r, *s), pd(*r, *p, *q), pd(*s, *p, *q))


def side_of(dx, dy):
    """Eight-way side of a vector in board coordinates, y down."""
    a = math.degrees(math.atan2(-dy, dx)) % 360
    return ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE'][int(((a + 22.5) % 360) // 45)]


def main():
    ensure_kipy()
    retry_busy()
    from kipy import KiCad
    from kipy.board_types import ArcTrack, BoardLayer, PadStackShape, Track, Via
    from kipy.geometry import Vector2

    ap = argparse.ArgumentParser()
    ap.add_argument('board_dir'); ap.add_argument('ref')
    ap.add_argument('step', choices=['groups', 'top', 'vias', 'straps', 'route', 'clear'])
    ap.add_argument('--group', help='top, route: the group name in fanout/<ref>.json')
    ap.add_argument('--band', type=float, default=2.4, help='mm a trace may drift across its direction')
    ap.add_argument('--via', type=float, default=0.25)
    ap.add_argument('--drill', type=float, default=0.15)
    ap.add_argument('--track', type=float, default=0.11)
    ap.add_argument('--clearance', type=float, default=0.10)
    ap.add_argument('--hole-clearance', type=float, default=0.20)
    ap.add_argument('--hole-to-hole', type=float, default=0.22)
    ap.add_argument('--power', default=r'^/?\+?\d+V\d*$', help='power net names, regex')
    ap.add_argument('--iters', type=int, default=60)
    ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--save', action='store_true')
    a = ap.parse_args()
    if not Path(a.board_dir).is_dir():
        raise Bad(f"no folder {a.board_dir}")
    if a.step in ('top', 'route') and not a.group:
        raise Bad(f"{a.step} needs --group")

    VD, VH, TW, CL, HCL, HH, M = a.via, a.drill, a.track, a.clearance, a.hole_clearance, a.hole_to_hole, 0.01
    VR = max(VD / 2 + CL, VH / 2 + HCL)          # via centre to other copper edge
    VV = round(max(VR + VD / 2, VH + HH), 3)    # via centre to via centre, the minimum: 0.40
    POWER = re.compile(a.power)

    try:
        b = KiCad(timeout_ms=10000).get_board()
    except Exception as e:
        raise Bad(f"no board open in KiCad: {e}")
    # KiCad 10 segfaults in the Schematic Editor's API handler, checkForBusy(), on a
    # board edit while the Schematic Editor is open (radar_2 crash-incidents 1.3, 1.4).
    if a.step != 'groups' and sch_open():
        close_schematic()
        if sch_open():
            raise Bad("the Schematic Editor is still open after save and close: "
                      "a board edit with it open crashes KiCad")
        print("Schematic Editor saved and closed")

    LF, L2, L4 = BoardLayer.BL_F_Cu, BoardLayer.BL_In2_Cu, BoardLayer.BL_In4_Cu
    LNAME = {LF: 'F.Cu', L2: 'In2.Cu', L4: 'In4.Cu'}
    ALLL = (LF, L2, L4)
    P = lambda o: (o.x / NM, o.y / NM)
    R3 = lambda p: (round(p[0], 3), round(p[1], 3))
    isg = lambda n: n.upper().endswith('GND')

    fps = b.get_footprints()
    fp = next((f for f in fps if f.reference_field.text.value == a.ref), None)
    if fp is None:
        raise Bad(f"no footprint {a.ref} on the open board")
    pads = list(fp.definition.pads)
    CX, CY = P(fp.position)
    xs = sorted({round(p.position.x / NM, 3) for p in pads})
    pitch = min(x2 - x1 for x1, x2 in zip(xs, xs[1:]) if x2 - x1 > 0.05)
    balls = [(p.number, p.net, *P(p.position)) for p in pads if p.net.name and 'unconnected' not in p.net.name]
    if not any(isg(bl[1].name) for bl in balls):
        raise Bad(f"{a.ref} has no GND ball")
    GND = next(bl[1] for bl in balls if isg(bl[1].name))
    sig = [bl for bl in balls if not isg(bl[1].name) and not POWER.match(bl[1].name)]
    pwr = [bl for bl in balls if POWER.match(bl[1].name)]
    netobj = {bl[1].name: bl[1] for bl in balls}
    ballpos = {R3((x, y)) for _, _, x, y in balls}
    ballnet = {R3((x, y)): net.name for _, net, x, y in balls}
    cy = [s for s in fp.definition.shapes if s.layer == BoardLayer.BL_F_CrtYd]
    if not cy:
        raise Bad(f"{a.ref} has no courtyard on F.Courtyard")
    bb = b.get_item_bounding_box(cy)[0]
    CXL, CYT = bb.pos.x / NM, bb.pos.y / NM
    CXR, CYB = CXL + bb.size.x / NM, CYT + bb.size.y / NM
    SPAN = max(max(abs(x - CX), abs(y - CY)) for _, _, x, y in balls) + pitch / 2
    print(f'{a.ref}: {len(sig)} signals, {len(pwr)} power balls, pitch {pitch:.2f}, '
          f'courtyard {CXR - CXL:.1f} x {CYB - CYT:.1f} mm')

    def mk_track(ly, p0, p1, w, net):
        t = Track(); t.layer = ly; t.width = int(round(w * NM)); t.net = net
        t.start = Vector2.from_xy(int(round(p0[0] * NM)), int(round(p0[1] * NM)))
        t.end = Vector2.from_xy(int(round(p1[0] * NM)), int(round(p1[1] * NM)))
        return t

    def mk_via(p, net):
        v = Via(); v.position = Vector2.from_xy(int(round(p[0] * NM)), int(round(p[1] * NM))); v.net = net
        v.padstack.drill.diameter = Vector2.from_xy(int(VH * NM), int(VH * NM))
        v.padstack.copper_layer(LF).size = Vector2.from_xy(int(VD * NM), int(VD * NM))
        return v

    def pad_r(p, ly):
        cl = p.padstack.copper_layer(ly) or (p.padstack.copper_layers[0] if p.padstack.copper_layers else None)
        if cl is None or not cl.size.x:
            return 1.0
        if cl.shape == PadStackShape.PSS_CIRCLE:
            return max(cl.size.x, cl.size.y) / 2 / NM
        return math.hypot(cl.size.x, cl.size.y) / 2 / NM

    def segs_of(t):
        s, e = P(t.start), P(t.end)
        if isinstance(t, ArcTrack):
            m = P(t.mid)
            return [(s, m), (m, e)]
        return [(s, e)]

    def write(remove, create):
        if a.dry_run:
            print('dry run: nothing written')
            return
        if remove:
            b.remove_items(remove)
        if create:
            b.create_items(create)
        if a.save:
            b.save(); print('saved')

    tracks = b.get_tracks()
    allv = b.get_vias()
    allpads = b.get_pads()
    R = max(CXR - CX, CX - CXL, CYB - CY, CY - CYT) + 5
    inR = lambda p: abs(p[0] - CX) < R and abs(p[1] - CY) < R
    vpos = {R3(P(v.position)) for v in allv}
    tends = {R3(p) for t in tracks for p in (P(t.start), P(t.end))}

    # ------------------------------------------------------------------ groups
    if a.step == 'groups':
        where = defaultdict(set)
        for f in fps:
            r = f.reference_field.text.value
            if r != a.ref:
                for p in f.definition.pads:
                    if p.net.name:
                        where[p.net.name].add(r)
        fpos = {f.reference_field.text.value: P(f.position) for f in fps}
        print('\n| # | Net | Ball | Parts at the other end, side |\n|---|---|---|---|')
        for k, (n, net, x, y) in enumerate(sorted(sig, key=lambda bl: bl[1].name), 1):
            far = ', '.join(f'{r} {side_of(fpos[r][0] - CX, fpos[r][1] - CY)}' for r in sorted(where[net.name])) or '—'
            print(f'| {k} | `{net.name}` | {n} | {far} |')
        print(f'\nWrite the groups to {Path(a.board_dir) / "fanout" / (a.ref + ".json")}:\n'
              '{"groups": [{"name": "ADC", "dir": "W", "nets": ["/ADC_SDIO", ...]}, ...]}')
        return

    # ------------------------------------------------------------------ vias
    if a.step == 'vias':
        new = [mk_via((x, y), net) for _, net, x, y in balls if R3((x, y)) not in vpos and R3((x, y)) not in tends]
        top = sum(1 for _, _, x, y in balls if R3((x, y)) not in vpos and R3((x, y)) in tends)
        print(f'ball vias: {len(new)} to place; {len(balls) - len(new) - top} already there; '
              f'{top} balls routed from the pad, left without')
        write([], new)
        return

    # ------------------------------------------------------------------ straps
    if a.step == 'straps':
        def is_strap(t):
            s0, s1 = R3(P(t.start)), R3(P(t.end))
            if not POWER.match(t.net.name) or not ballnet.get(s0) == ballnet.get(s1) == t.net.name:
                return False
            d = math.dist(s0, s1)
            return abs(d - pitch) < 0.01 or abs(d - pitch * math.sqrt(2)) < 0.01
        old = [t for t in tracks if t.layer == LF and is_strap(t)]
        oldid = {t.id.value for t in old}
        vall = [(*P(v.position), v.net.name, max(v.padstack.copper_layer(LF).size.x / NM / 2 + CL, VH / 2 + HCL))
                for v in allv if inR(P(v.position))]
        segs = [(s, e, t.width / NM, t.net.name) for t in tracks if t.layer == LF and t.id.value not in oldid
                for s, e in segs_of(t) if inR(s) or inR(e)]
        fpads = [(*P(p.position), p.net.name, pad_r(p, LF)) for p in allpads
                 if inR(P(p.position)) and LF in p.padstack.layers]
        par = {}

        def root(x):
            par.setdefault(x, x)
            while par[x] != x:
                par[x] = par[par[x]]; x = par[x]
            return x

        def fits(p0, p1, net):
            return all(nn == net or pd(x, y, *p0, *p1) >= vr + TW / 2 for x, y, nn, vr in vall) and \
                all(nn == net or pd(x, y, *p0, *p1) >= r + CL + TW / 2 for x, y, nn, r in fpads) and \
                all(nn == net or segd(p0, p1, s0, s1) >= TW / 2 + w / 2 + CL for s0, s1, w, nn in segs)

        cand = []
        for i, (n1, net1, x1, y1) in enumerate(pwr):
            for n2, net2, x2, y2 in pwr[i + 1:]:
                if net1.name == net2.name:
                    d = math.hypot(x2 - x1, y2 - y1)
                    if abs(d - pitch) < 0.01:
                        cand.append((0, n1, n2, net1, (x1, y1), (x2, y2)))
                    elif abs(d - pitch * math.sqrt(2)) < 0.01:
                        cand.append((1, n1, n2, net1, (x1, y1), (x2, y2)))
        straps, nofit = [], 0
        for kind, n1, n2, net, p0, p1 in sorted(cand, key=lambda c: c[0]):
            if kind == 1 and root(n1) == root(n2):
                continue
            if fits(p0, p1, net.name):
                straps.append([p0, p1, TW, net]); segs.append((p0, p1, TW, net.name)); par[root(n1)] = root(n2)
            else:
                nofit += 1
        for s in straps:
            lim = 0.25
            for s0, s1, w, nn in segs:
                if (s0, s1) == (s[0], s[1]):
                    continue
                d = segd(s[0], s[1], s0, s1)
                if d > 1.5:
                    continue
                if nn == s[3].name:
                    if d == 0 or {s0, s1} & {s[0], s[1]}:
                        continue
                    lim = min(lim, d - w / 2)
                else:
                    lim = min(lim, (d - CL) / 2 if POWER.match(nn) else d - CL - w / 2)
            for x, y, nn, vr in vall:
                if nn != s[3].name:
                    lim = min(lim, pd(x, y, *s[0], *s[1]) - vr)
            for x, y, nn, r in fpads:
                if nn != s[3].name:
                    lim = min(lim, pd(x, y, *s[0], *s[1]) - r - CL)
            s[2] = max(TW, math.floor(2 * lim * 100) / 100)
        print(f'straps: remove {len(old)}, draw {len(straps)} on F.Cu; {nofit} pairs without clearance')
        write(old, [mk_track(LF, p0, p1, w, net) for p0, p1, w, net in straps])
        return

    # ------------------------------------------------------------------ clear
    if a.step == 'clear':
        signets = {bl[1].name for bl in sig}
        strap = lambda t: POWER.match(t.net.name) and ballnet.get(R3(P(t.start))) == ballnet.get(R3(P(t.end))) == t.net.name
        rm_t = [t for t in tracks if t.layer in ALLL and inR(P(t.start)) and inR(P(t.end))
                and (t.net.name in signets or strap(t))]
        rm_b = [v for v in allv if R3(P(v.position)) in ballpos]
        rm_s = [v for v in allv if v.net.name in signets and R3(P(v.position)) not in ballpos and inR(P(v.position))]
        sp = [P(v.position) for v in rm_s]
        rm_g = [v for v in allv if isg(v.net.name) and R3(P(v.position)) not in ballpos
                and any(abs(math.dist(P(v.position), q) - VV) < 0.005 for q in sp)]
        print(f'clear: {len(rm_t)} tracks, {len(rm_b)} ball vias, {len(rm_s)} signal vias, {len(rm_g)} GND vias')
        write(rm_t + rm_b + rm_s + rm_g, [])
        return

    # ------------------------------------------------------------------ top, route
    gfile = Path(a.board_dir) / 'fanout' / f'{a.ref}.json'
    if not gfile.exists():
        raise Bad(f"no {gfile}: run groups and write it")
    groups = {g['name']: g for g in json.loads(gfile.read_text())['groups']}
    if a.group not in groups:
        raise Bad(f"no group {a.group} in {gfile}: {', '.join(groups)}")
    grp = groups[a.group]
    D = grp['dir'].upper()
    if D not in DIRS:
        raise Bad(f"group {a.group}: dir {D} is not one of {' '.join(DIRS)}")
    dvx, dvy = DIRS[D]
    straight = dvx == 0 or dvy == 0
    gnets = set(grp['nets'])
    gballs = [bl for bl in sig if bl[1].name in gnets]
    missing = gnets - {bl[1].name for bl in gballs}
    if missing:
        raise Bad(f"group {a.group}: no signal ball of {a.ref} on {', '.join(sorted(missing))}")
    has_via = lambda bl: R3((bl[2], bl[3])) in vpos
    if a.step == 'top':
        mine, LAY = [bl for bl in gballs if not has_via(bl)], [LF]
    else:
        bare = [bl[1].name for bl in gballs if not has_via(bl) and R3((bl[2], bl[3])) not in tends]
        if bare:
            raise Bad(f"group {a.group}: balls with no via and no route: {', '.join(bare)}; run vias first")
        mine, LAY = [bl for bl in gballs if has_via(bl)], [LF, L2, L4]
    ENDL = [L for L in LAY if L != L4]          # a trace ends on F.Cu or In2.Cu, never on In4.Cu
    tnets = {bl[1].name for bl in mine}

    # this step's own earlier output for these nets: tracks, vias off the balls, their GND vias
    old_t = [t for t in tracks if t.net.name in tnets and t.layer in ALLL and inR(P(t.start)) and inR(P(t.end))]
    old_v = [v for v in allv if v.net.name in tnets and R3(P(v.position)) not in ballpos and inR(P(v.position))]
    ev = [P(v.position) for v in old_v]
    old_g = [v for v in allv if isg(v.net.name) and R3(P(v.position)) not in ballpos
             and any(abs(math.dist(P(v.position), q) - VV) < 0.005 for q in ev)]
    old = old_t + old_v + old_g
    oldid = {o.id.value for o in old}

    obs = {L: [] for L in ALLL}     # (a, b, copper radius, net, kind)
    for t in tracks:
        if t.id.value not in oldid and t.layer in obs:
            for s, e in segs_of(t):
                if inR(s) or inR(e):
                    obs[t.layer].append((s, e, t.width / NM / 2, t.net.name, 'trk'))
    for v in allv:
        p = P(v.position)
        if v.id.value not in oldid and inR(p):
            for L in obs:
                obs[L].append((p, p, v.padstack.copper_layer(LF).size.x / NM / 2, v.net.name, 'via'))
    for p in allpads:
        pp = P(p.position)
        if inR(pp):
            for L in obs:
                if L in p.padstack.layers:
                    obs[L].append((pp, pp, pad_r(p, L), p.net.name, 'pad'))

    X0, Y0 = CX - R, CY - R
    S = int(round(2 * R / G)) + 1
    xy = lambda i, j: (X0 + i * G, Y0 + j * G)
    ij = lambda x, y: (int(round((x - X0) / G)), int(round((y - Y0) / G)))

    def cells_near(s, e, r):
        i0, j0 = ij(min(s[0], e[0]) - r, min(s[1], e[1]) - r)
        i1, j1 = ij(max(s[0], e[0]) + r, max(s[1], e[1]) + r)
        return [(i, j) for i in range(max(0, i0 - 1), min(S, i1 + 2)) for j in range(max(0, j0 - 1), min(S, j1 + 2))
                if pd(*xy(i, j), *s, *e) < r]
    disk = lambda p, r: cells_near(p, p, r)

    fixed = {L: defaultdict(set) for L in LAY}    # cell -> nets whose copper keeps a trace centre out
    novia = defaultdict(set)                      # cell -> nets whose copper keeps a through via out
    for L in ALLL:
        for s, e, r, nn, kind in obs[L]:
            if L in fixed:
                rr = r + CL + TW / 2 + M
                if kind == 'via':
                    rr = max(rr, VH / 2 + HCL + TW / 2 + M)
                for c in cells_near(s, e, rr):
                    fixed[L][c].add(nn)
            rv = (r + VR if kind != 'via' else max(r + VD / 2 + CL, VH + HH)) + M
            for c in cells_near(s, e, rv):
                novia[c].add(nn)
    via_ok = lambda p, net: not (novia.get(ij(*p), set()) - {net})

    # end sites outside the courtyard on each side the direction faces: columns from just
    # past the edge outward 0.8 mm apart, rows 0.8 mm apart, alternate columns offset 0.4.
    # Nearer costs less: the path is shorter. The GND via is 0.40 mm from the end via toward the part.
    edges = []
    if dvx < 0: edges.append(('W', CXL, (-1, 0)))
    if dvx > 0: edges.append(('E', CXR, (1, 0)))
    if dvy < 0: edges.append(('N', CYT, (0, -1)))
    if dvy > 0: edges.append(('S', CYB, (0, 1)))
    sites = []
    for name, pos, (nx, ny) in edges:
        for k in range(6):
            off = pos + (nx + ny) * (VD / 2 + 0.05 + 0.8 * k)
            lo, hi = (CYT, CYB) if nx else (CXL, CXR)
            t = lo + 0.4 * (k % 2)
            while t <= hi + 1e-6:
                p = (off, t) if nx else (t, off)
                sites.append((R3(p), R3((p[0] - nx * VV, p[1] - ny * VV)), name))
                t += 0.8
    usable = {}
    for st in sites:
        if via_ok(st[1], GND.name):
            ok = {bl[1].name for bl in mine if via_ok(st[0], bl[1].name)}
            if ok:
                usable[st] = ok
    print(f'{a.step} {a.group}: {len(mine)} nets toward {D} on {", ".join(LNAME[L] for L in LAY)}; '
          f'{len(usable)} end sites')
    if not mine:
        write(old, [])
        return

    R_VIA = VR + TW / 2 + M          # via centre to another trace's centre line
    R_TRK = TW + CL + M              # trace centre line to another's
    D_TRK = [(di, dj) for di in range(-3, 4) for dj in range(-3, 4) if math.hypot(di, dj) * G < R_TRK]
    D_VIA = [(di, dj) for di in range(-4, 5) for dj in range(-4, 5) if math.hypot(di, dj) * G < R_VIA]
    MOVES = [k for k, (di, dj) in enumerate(D8)
             if di * dvx + dj * dvy > 0 or (straight and di * dvx + dj * dvy == 0)]    # never back
    goal_of = defaultdict(list)
    for st in usable:
        goal_of[ij(*st[0])].append(st)
    gnd_cells = {st: set(disk(st[1], R_VIA)) for st in usable}
    nid = {bl[1].name: k + 1 for k, bl in enumerate(mine)}
    GID = -1
    occ = {L: defaultdict(Counter) for L in LAY}
    hist = {L: defaultdict(float) for L in LAY}
    route = {}
    infield = lambda c: max(abs(xy(*c)[0] - CX), abs(xy(*c)[1] - CY)) < SPAN
    un = math.hypot(dvx, dvy)
    back = (-dvx / un * VV, -dvy / un * VV)       # GND via offset from a layer-change via, toward the part
    W4 = 2.0                                      # In4.Cu costs this much more per step: the plane is cut little
    CHG = 8.0                                     # a layer change costs this much

    # a route: {'runs': [(L, cells)], 'site': end site, 'chg': (via, GND via) or None}
    def footprint(net, r):
        k = nid[net]
        out = [(L, (c[0] + di, c[1] + dj), k) for L, cells in r['runs'] for c in cells for di, dj in D_TRK]
        vias = [(r['site'][0], k), (r['site'][1], GID)]
        if r['chg']:
            vias += [(r['chg'][0], k), (r['chg'][1], GID)]
        for p, kk in vias:
            c0 = ij(*p)
            out += [(LL, (c0[0] + di, c0[1] + dj), kk) for LL in LAY for di, dj in D_VIA]
        return out

    def add(net, r, sign):
        for L, c, k in footprint(net, r):
            occ[L][c][k] += sign
            if occ[L][c][k] == 0:
                del occ[L][c][k]

    def astar(net, starts, start, pres, allow=None, site_ok=None):
        """Cheapest route for net from its ball: on one of `starts`; from In4.Cu one change, outside
        the ball field, to F.Cu or In2.Cu; ending at a usable site on F.Cu or In2.Cu."""
        k = nid[net]
        bx, by = start
        band = int(round(a.band / G))
        off_band = lambda c: straight and ((dvy and abs(c[0] - bx) > band) or (dvx and abs(c[1] - by) > band))
        blocked = lambda L, c: bool(fixed[L].get(c, set()) - {net})
        fit = lambda st: net in usable[st] and (site_ok is None or site_ok(st))
        goals = {c for c, sts in goal_of.items() if not off_band(c) and any(fit(s) for s in sts)}
        if not goals:
            return None
        gl = list(goals)
        h = lambda c: min(abs(c[0] - g[0]) + abs(c[1] - g[1]) for g in gl) * 0.9

        def cost(L, c):
            o = occ[L].get(c)
            n_o = sum(v for kk, v in o.items() if kk != k) if o else 0
            return (1 + hist[L][c]) * (1 + pres * n_o)

        def via_cost(c):
            n_o = 0
            for LL in LAY:
                o = occ[LL].get(c)
                n_o += sum(v for kk, v in o.items() if kk != k) if o else 0
            return 1 + pres * n_o

        pq, best, prev = [], {}, {}
        for L in starts:
            s0 = (L, start, -1)
            best[s0] = 0.0
            heapq.heappush(pq, (h(start), 0.0, s0))
        while pq:
            f, g, s_ = heapq.heappop(pq)
            if g > best.get(s_, 1e18):
                continue
            L, c, d = s_
            if L in ENDL and c in goals and c != start:
                path = [s_]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                path = path[::-1]
                cells = {q[1] for q in path}
                for st in goal_of[c]:
                    if fit(st) and not (cells & gnd_cells[st]):
                        return path, st, g
            if L == L4 and not infield(c) and c != start:     # hand off to F.Cu or In2.Cu
                pg = (xy(*c)[0] + back[0], xy(*c)[1] + back[1])
                if via_ok(xy(*c), net) and via_ok(pg, GND.name):
                    for LT in ENDL:
                        if blocked(LT, c):
                            continue
                        ns = (LT, c, -1)
                        g2 = g + CHG * via_cost(c)
                        if g2 < best.get(ns, 1e18):
                            best[ns] = g2; prev[ns] = s_
                            heapq.heappush(pq, (g2 + h(c), g2, ns))
            for nd in MOVES:
                di, dj = D8[nd]
                n = (c[0] + di, c[1] + dj)
                if not (0 <= n[0] < S and 0 <= n[1] < S) or blocked(L, n) or off_band(n) or \
                        (allow is not None and n not in allow):
                    continue
                if di and dj and (blocked(L, (c[0] + di, c[1])) or blocked(L, (c[0], c[1] + dj))):
                    continue
                if L == L4 and not infield(c):        # In4.Cu ends at the edge of the ball field
                    continue
                across = di * dvx + dj * dvy == 0
                g2 = g + (1.4142 if di and dj else 1) * (1.5 if across else 1) * (W4 if L == L4 else 1) * cost(L, n) \
                    + (2 if d != -1 and nd != d else 0)
                ns = (L, n, nd)
                if g2 < best.get(ns, 1e18):
                    best[ns] = g2; prev[ns] = s_
                    heapq.heappush(pq, (g2 + h(n), g2, ns))
        return None

    def as_route(path, st):
        runs, chg = [], None
        for L, c, d in path:
            if runs and runs[-1][0] == L:
                runs[-1][1].append(c)
            else:
                if runs:
                    p = xy(*c)
                    chg = (R3(p), R3((p[0] + back[0], p[1] + back[1])))
                runs.append((L, [c]))
        return {'runs': runs, 'site': st, 'chg': chg}

    # differential pairs: <stem>_P and <stem>_N both here. One layer, adjacent, end sites side by side
    byname = {bl[1].name: bl for bl in mine}
    pairs = {}
    for n in byname:
        m = re.match(r'(.+)_P$', n)
        if m and m.group(1) + '_N' in byname:
            pairs[n] = m.group(1) + '_N'
    paired = set(pairs) | set(pairs.values())
    units = [(byname[p], byname[n]) for p, n in pairs.items()] + [(bl,) for bl in mine if bl[1].name not in paired]
    CPL = 2 * R_TRK - G                 # outside the ball field: no third trace fits between
    CPL_IN = pitch + G                  # in the ball field: the next channel, one ball row between

    def route_unit(u, pres):
        if len(u) == 1:
            bl = u[0]
            r = astar(bl[1].name, LAY, ij(bl[2], bl[3]), pres)
            return r and {bl[1].name: as_route(r[0], r[1])}
        bp, bn = u
        best = None
        for L in ENDL:                  # a pair stays on one layer, never In4.Cu
            rp = astar(bp[1].name, [L], ij(bp[2], bp[3]), pres)
            if not rp or len({q[0] for q in rp[0]}) > 1:
                continue
            routp = as_route(rp[0], rp[1])
            add(bp[1].name, routp, +1)
            allow = set()
            for c in routp['runs'][0][1]:
                allow.update(disk(xy(*c), CPL))
                allow.update(q for q in disk(xy(*c), CPL_IN) if infield(q))
            allow.update(disk((bn[2], bn[3]), pitch * 1.5))
            allow.update(disk(rp[1][0], 0.8 + CPL))
            beside = lambda st, sp=rp[1]: st[2] == sp[2] and st[0] != sp[0] and math.dist(st[0], sp[0]) < 0.9 + 1e-6
            rn = astar(bn[1].name, [L], ij(bn[2], bn[3]), pres, allow, beside)
            add(bp[1].name, routp, -1)
            if not rn:                  # no room beside the P trace: make that route cost more
                for c in routp['runs'][0][1]:
                    hist[L][c] += 0.5
            elif best is None or rp[2] + rn[2] < best[0]:
                best = (rp[2] + rn[2], {bp[1].name: routp, bn[1].name: as_route(rn[0], rn[1])})
        return best and best[1]

    def vias_of(net, r):
        out = [(net, r['site'][0]), (net + ' GND', r['site'][1])]
        if r['chg']:
            out += [(net, r['chg'][0]), (net + ' GND', r['chg'][1])]
        return out

    def conflicts():
        """Nets whose centre line lies in another's footprint, or whose vias crowd another's."""
        bad = set()
        for net, r in route.items():
            if any(any(x != nid[net] for x in occ[L].get(c, {})) for L, cells in r['runs'] for c in cells):
                bad.add(net)
        vs = [(net, p) for n_, r in route.items() for net, p in vias_of(n_, r)]
        for i, (n1, p1) in enumerate(vs):
            for n2, p2 in vs[i + 1:]:
                if n1.split(' ')[0] != n2.split(' ')[0] and math.dist(p1, p2) < VV - 1e-6:
                    bad |= {n1.split(' ')[0], n2.split(' ')[0]}
        return bad

    lead = lambda u: max((bl[2] - CX) * dvx + (bl[3] - CY) * dvy for bl in u)
    order = sorted(units, key=lambda u: -lead(u))      # nearest the edge first
    pres = 0.5
    for it in range(a.iters):
        t0 = time.time()
        for u in order:
            for bl in u:
                if bl[1].name in route:
                    add(bl[1].name, route.pop(bl[1].name), -1)
            got = route_unit(u, pres)
            for net, r in (got or {}).items():
                route[net] = r
                add(net, r, +1)
        for L in LAY:
            for c, o in occ[L].items():
                if len(o) > 1:
                    hist[L][c] += 1
        bad = conflicts()
        unrouted = [bl[1].name for bl in mine if bl[1].name not in route]
        print(f'  pass {it + 1}: conflicts {len(bad)}, unrouted {len(unrouted)}, {time.time() - t0:.0f} s')
        if not bad and not unrouted:
            break
        pres *= 1.6
        hot = [u for u in order if any(bl[1].name in bad or bl[1].name not in route for bl in u)]
        order = hot + [u for u in order if u not in hot]

    # keep a conflict-free set: drop the unit in most conflict until none remain
    unit_of = {bl[1].name: k for k, u in enumerate(units) for bl in u}
    dropped = [bl[1].name for bl in mine if bl[1].name not in route]
    while True:
        bad = conflicts()
        if not bad:
            break
        u = units[max({unit_of[n] for n in bad}, key=lambda k: (sum(bl[1].name in bad for bl in units[k]), -lead(units[k])))]
        for bl in u:
            if bl[1].name in route:
                add(bl[1].name, route.pop(bl[1].name), -1)
                dropped.append(bl[1].name)
    for p, n in pairs.items():         # a pair is both or neither
        for x, y in ((p, n), (n, p)):
            if x in route and y not in route:
                add(x, route.pop(x), -1); dropped.append(x)

    # ---- clean-up: draw each run taut, octilinear, never back, clear of all copper but its own net
    def corners(p):
        pts = [p[0]]
        for a_, c, d in zip(p, p[1:], p[2:]):
            if (c[0] - a_[0], c[1] - a_[1]) != (d[0] - c[0], d[1] - c[1]):
                pts.append(c)
        pts.append(p[-1])
        return pts

    poly = {}                          # net -> [(L, [points])]
    for bl in mine:
        net = bl[1].name
        if net not in route:
            continue
        r = route[net]
        runs = []
        for k, (L, cells) in enumerate(r['runs']):
            pts = [xy(*c) for c in corners(cells)] if len(cells) > 1 else [xy(*cells[0])] * 2
            if k == 0:
                pts[0] = (bl[2], bl[3])
            if k == len(r['runs']) - 1:
                pts[-1] = r['site'][0]
            if k < len(r['runs']) - 1:
                pts[-1] = r['chg'][0]
            if k > 0:
                pts[0] = r['chg'][0]
            runs.append((L, pts))
        poly[net] = runs

    BK = 1.0
    bucket = {L: defaultdict(list) for L in LAY}

    def put(L, item):
        s, e, w = item[0], item[1], item[2]
        for bi in range(int((min(s[0], e[0]) - w) // BK), int((max(s[0], e[0]) + w) // BK) + 1):
            for bj in range(int((min(s[1], e[1]) - w) // BK), int((max(s[1], e[1]) + w) // BK) + 1):
                bucket[L][(bi, bj)].append(item)

    for L in LAY:                       # fixed copper: (a, b, keep-out from a trace centre line, net)
        for s, e, r, nn, kind in obs[L]:
            kr = r + CL + TW / 2 if kind != 'via' else max(r + CL, VH / 2 + HCL) + TW / 2
            put(L, (s, e, kr, nn, None))
    for net, r in route.items():        # new vias, every layer
        for nm, p in vias_of(net, r):
            for L in LAY:
                put(L, (p, p, VR + TW / 2, 'GND' if nm.endswith('GND') else net, None))
    gen = Counter()                     # net -> generation of its traces now in the buckets

    def put_runs(net):
        gen[net] += 1
        for L, pts in poly[net]:
            for s, e in zip(pts, pts[1:]):
                put(L, (s, e, TW + CL, net, (net, gen[net])))

    for net in poly:
        put_runs(net)

    def clear_seg(L, s, e, net):
        if (e[0] - s[0]) * dvx + (e[1] - s[1]) * dvy < -1e-9:
            return False
        if not straight and (e[0] - s[0]) * dvx + (e[1] - s[1]) * dvy == 0 and s != e:
            return False
        seen = set()
        for bi in range(int((min(s[0], e[0]) - 0.6) // BK), int((max(s[0], e[0]) + 0.6) // BK) + 1):
            for bj in range(int((min(s[1], e[1]) - 0.6) // BK), int((max(s[1], e[1]) + 0.6) // BK) + 1):
                for it_ in bucket[L].get((bi, bj), ()):
                    if id(it_) in seen:
                        continue
                    seen.add(id(it_))
                    s2, e2, kr, nn, tag = it_
                    if nn == net or (tag is not None and gen[tag[0]] != tag[1]):
                        continue
                    if segd(s, e, s2, e2) < kr - 1e-4:
                        return False
        return True

    octi = lambda s, e: abs(e[0] - s[0]) < 1e-6 or abs(e[1] - s[1]) < 1e-6 or abs(abs(e[0] - s[0]) - abs(e[1] - s[1])) < 1e-6

    def taut(L, pts, net):
        out, i = [pts[0]], 0
        while i < len(pts) - 1:
            step = None
            for j in range(len(pts) - 1, i, -1):
                s, e = pts[i], pts[j]
                if octi(s, e) and clear_seg(L, s, e, net):
                    step = (j, [e]); break
                dx, dy = e[0] - s[0], e[1] - s[1]
                dd = min(abs(dx), abs(dy))
                sx, sy = math.copysign(1, dx), math.copysign(1, dy)
                for m in ((s[0] + sx * dd, s[1] + sy * dd), (e[0] - sx * dd, e[1] - sy * dd)):
                    if clear_seg(L, s, m, net) and clear_seg(L, m, e, net):
                        step = (j, [m, e]); break
                if step:
                    break
            if not step:
                step = (i + 1, [pts[i + 1]])
            out += step[1]
            i = step[0]
        return [p for k, p in enumerate(out) if k == 0 or math.dist(p, out[k - 1]) > 1e-6]

    before = sum(len(pts) - 1 for runs in poly.values() for L, pts in runs)
    for _ in range(2):
        for net in list(poly):
            new = [(L, taut(L, pts, net)) for L, pts in poly[net]]
            poly[net] = new
            put_runs(net)
    after = sum(len(pts) - 1 for runs in poly.values() for L, pts in runs)

    create, rows = [], []
    for bl in mine:
        net = bl[1].name
        if net not in route:
            continue
        r = route[net]
        for L, pts in poly[net]:
            create += [mk_track(L, p0, p1, TW, netobj[net]) for p0, p1 in zip(pts, pts[1:]) if p0 != p1]
        create += [mk_via(r['site'][0], netobj[net]), mk_via(r['site'][1], GND)]
        if r['chg']:
            create += [mk_via(r['chg'][0], netobj[net]), mk_via(r['chg'][1], GND)]
        allp = [p for L, pts in poly[net] for p in pts]
        drift = max((abs(x - bl[2]) if not dvx else abs(y - bl[3]) if not dvy else 0) for x, y in allp)
        rows.append((net, bl[0], ' then '.join(LNAME[L] for L, _ in poly[net]), r['site'][0], drift))
    print('\n| # | Net | Ball | Layers | End via, from centre | Drift |\n|---|---|---|---|---|---|')
    for k, (net, n, ln, p, dr) in enumerate(sorted(rows), 1):
        print(f'| {k} | `{net}` | {n} | {ln} | {p[0] - CX:+.2f}, {p[1] - CY:+.2f} | {dr:.2f} |')
    if pairs:
        done = [f'{p}/{n[n.rfind("_"):]} {LNAME[route[p]["runs"][0][0]]}' for p, n in sorted(pairs.items()) if p in route]
        print('\npairs routed: ' + (', '.join(done) or 'none'))
    n4 = sum(1 for r in route.values() if r['chg'])
    print(f'\nsegments: {before} before clean-up, {after} after; {n4} escapes on In4.Cu')
    if a.step == 'top':
        print(f'top: {len(rows)} of {len(mine)} on F.Cu; left for route: {", ".join(sorted(set(dropped))) or "none"}')
    else:
        verdict = 'PASS' if len(rows) == len(mine) else 'FAIL'
        print(f'{verdict}: routed {len(rows)} of {len(mine)}; not routed: {", ".join(sorted(set(dropped))) or "none"}')
    print(f'remove {len(old_t)} tracks, {len(old_v)} vias, {len(old_g)} GND vias; '
          f'create {sum(isinstance(c, Track) for c in create)} tracks, {sum(isinstance(c, Via) for c in create)} vias')
    write(old, create)


if __name__ == "__main__":
    main()
