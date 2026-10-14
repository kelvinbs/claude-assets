#!/usr/bin/env python3
"""bga-fanout — fan out a ball grid array on the board open in KiCad, in steps.

    bga-fanout.py <board-dir> <ref> vias   [--save] [--dry-run]
    bga-fanout.py <board-dir> <ref> straps [--save] [--dry-run]
    bga-fanout.py <board-dir> <ref> groups
    bga-fanout.py <board-dir> <ref> route --group NAME [--band MM] [--save] [--dry-run]

vias    a via in every connected ball that has none.
straps  adjacent power balls of one net joined on F.Cu where clearance allows,
        widened up to 0.5 mm. The part's straps are redrawn.
groups  each signal net with the parts at its other end and the side each part
        sits on. The LLM groups the nets from this and the record and writes
        <board-dir>/fanout/<ref>.json.
route   one group from that file: every trace leaves its ball heading only in
        the group's direction, on F.Cu or In2.Cu, and ends in a via on or just
        past the courtyard edge on that side; a GND via 0.40 mm from each end
        via, toward the part. The group's own earlier fan-out is removed first.
        Signals negotiate cells and end vias (PathFinder).

One read of the board, at most one remove and one create. Saved only with --save.
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
G = 0.1               # mm, routing grid
DIRS = {'N': (0, -1), 'S': (0, 1), 'E': (1, 0), 'W': (-1, 0),
        'NE': (1, -1), 'NW': (-1, -1), 'SE': (1, 1), 'SW': (-1, 1)}


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"bga-fanout: {message}")


def ensure_kipy():
    """Run under the tool's venv, with kicad-python in it."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not PY.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True,
                       capture_output=True)
    probe = subprocess.run([str(PY), "-c", "import kipy"], capture_output=True)
    if probe.returncode != 0:
        run = subprocess.run([str(VENV / "bin" / "pip"), "install", "-q",
                              "kicad-python"], capture_output=True, text=True)
        if run.returncode != 0:
            raise Bad("could not install kicad-python: "
                      + (run.stderr or run.stdout).strip()[-200:])
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


KICAD = 'first process whose bundle identifier is "org.kicad.kicad"'
SCH = 'first window whose name contains "Schematic Editor"'


def osa(*lines):
    """Run AppleScript lines through System Events on KiCad's process."""
    args = []
    for line in ('tell application "System Events" to tell ' + KICAD,
                 *lines, 'end tell'):
        args += ["-e", line]
    run = subprocess.run(["osascript", *args], capture_output=True, text=True)
    return run.returncode, run.stdout.strip()


def sch_open():
    """The Schematic Editor's window is on screen. Its lock file is not
    proof: a crash leaves the lock behind."""
    code, out = osa(f'  return exists ({SCH})')
    if code != 0:
        raise Bad("cannot read KiCad's windows through System Events")
    return out == "true"


def close_schematic():
    """Save and close the Schematic Editor window, macOS, through System
    Events: raise it, Cmd+S only once it is the front window, then its own
    close button, never Cmd+W. Waits up to 15 s for the window to go."""
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
    o = lambda a_, b_, c: (b_[0] - a_[0]) * (c[1] - a_[1]) - (b_[1] - a_[1]) * (c[0] - a_[0])
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
    ap.add_argument('step', choices=['vias', 'straps', 'groups', 'route'])
    ap.add_argument('--group', help='route: the group name in fanout/<ref>.json')
    ap.add_argument('--band', type=float, default=1.6,
                    help='route: mm a trace may drift across its direction')
    ap.add_argument('--via', type=float, default=0.25)
    ap.add_argument('--drill', type=float, default=0.15)
    ap.add_argument('--track', type=float, default=0.11)
    ap.add_argument('--clearance', type=float, default=0.10)
    ap.add_argument('--hole-clearance', type=float, default=0.20)
    ap.add_argument('--hole-to-hole', type=float, default=0.22)
    ap.add_argument('--power', default=r'^/?\+?\d+V\d*$', help='power net names, regex')
    ap.add_argument('--iters', type=int, default=40)
    ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--save', action='store_true')
    a = ap.parse_args()
    if not Path(a.board_dir).is_dir():
        raise Bad(f"no folder {a.board_dir}")
    if a.step == 'route' and not a.group:
        raise Bad("route needs --group")

    VD, VH, TW, CL, HCL, HH, M = a.via, a.drill, a.track, a.clearance, a.hole_clearance, a.hole_to_hole, 0.01
    VR = max(VD / 2 + CL, VH / 2 + HCL)          # via centre to other copper edge
    VV = round(max(VR + VD / 2, VH + HH), 3)    # via centre to via centre: the minimum, 0.40
    POWER = re.compile(a.power)
    LF, L2 = BoardLayer.BL_F_Cu, BoardLayer.BL_In2_Cu
    LNAME = {LF: 'F.Cu', L2: 'In2.Cu'}

    try:
        b = KiCad(timeout_ms=10000).get_board()
    except Exception as e:
        raise Bad(f"no board open in KiCad: {e}")
    # KiCad 10 segfaults in the Schematic Editor's API handler,
    # checkForBusy(), on a board edit while the Schematic Editor is open
    # (radar_2 crash-incidents 1.3, 1.4). Its window says it is open.
    if a.step != 'groups' and sch_open():
        close_schematic()
        if sch_open():
            raise Bad("the Schematic Editor is still open after save and "
                      "close: a board edit with it open crashes KiCad")
        print("Schematic Editor saved and closed")

    P = lambda o: (o.x / NM, o.y / NM)
    R3 = lambda p: (round(p[0], 3), round(p[1], 3))
    fps = b.get_footprints()
    fp = next((f for f in fps if f.reference_field.text.value == a.ref), None)
    if fp is None:
        raise Bad(f"no footprint {a.ref} on the open board")
    pads = list(fp.definition.pads)
    CX, CY = P(fp.position)
    xs = sorted({round(p.position.x / NM, 3) for p in pads})
    pitch = min(x2 - x1 for x1, x2 in zip(xs, xs[1:]) if x2 - x1 > 0.05)
    isg = lambda n: n.upper().endswith('GND')
    balls = [(p.number, p.net, *P(p.position)) for p in pads
             if p.net.name and 'unconnected' not in p.net.name]
    ballpos = {R3((x, y)) for _, _, x, y in balls}
    ballnet = {R3((x, y)): net.name for _, net, x, y in balls}
    if not any(isg(bl[1].name) for bl in balls):
        raise Bad(f"{a.ref} has no GND ball")
    GND = next(bl[1] for bl in balls if isg(bl[1].name))
    sig = [bl for bl in balls if not isg(bl[1].name) and not POWER.match(bl[1].name)]
    pwr = [bl for bl in balls if POWER.match(bl[1].name)]
    netobj = {bl[1].name: bl[1] for bl in balls}
    cy = [s for s in fp.definition.shapes if s.layer == BoardLayer.BL_F_CrtYd]
    if not cy:
        raise Bad(f"{a.ref} has no courtyard on F.Courtyard")
    bb = b.get_item_bounding_box(cy)[0]
    CXL, CYT = bb.pos.x / NM, bb.pos.y / NM
    CXR, CYB = CXL + bb.size.x / NM, CYT + bb.size.y / NM
    print(f'{a.ref}: {len(sig)} signals, {len(pwr)} power balls, pitch {pitch:.2f}, '
          f'courtyard {CXR - CXL:.1f} x {CYB - CYT:.1f} mm')

    def mk_track(ly, p0, p1, w, net):
        t = Track(); t.layer = ly; t.width = int(round(w * NM)); t.net = net
        t.start = Vector2.from_xy(int(round(p0[0] * NM)), int(round(p0[1] * NM)))
        t.end = Vector2.from_xy(int(round(p1[0] * NM)), int(round(p1[1] * NM)))
        return t

    def mk_via(x, y, net):
        v = Via(); v.position = Vector2.from_xy(int(round(x * NM)), int(round(y * NM))); v.net = net
        v.padstack.drill.diameter = Vector2.from_xy(int(VH * NM), int(VH * NM))
        v.padstack.copper_layer(BoardLayer.BL_F_Cu).size = Vector2.from_xy(int(VD * NM), int(VD * NM))
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

    # ---------------------------------------------------------------- vias
    if a.step == 'vias':
        have = {R3(P(v.position)) for v in allv}
        new = [mk_via(x, y, net) for _, net, x, y in balls if R3((x, y)) not in have]
        print(f'ball vias: {len(new)} to place, {len(balls) - len(new)} already there')
        write([], new)
        return

    # ---------------------------------------------------------------- groups
    if a.step == 'groups':
        where = {}
        for f in fps:
            r = f.reference_field.text.value
            if r == a.ref:
                continue
            for p in f.definition.pads:
                if p.net.name:
                    where.setdefault(p.net.name, set()).add(r)
        fpos = {f.reference_field.text.value: P(f.position) for f in fps}
        print(f'\n| # | Net | Ball | Parts at the other end, side |\n|---|---|---|---|')
        for k, (n, net, x, y) in enumerate(sorted(sig, key=lambda bl: bl[1].name), 1):
            refs = sorted(where.get(net.name, ()))
            far = ', '.join(f'{r} {side_of(fpos[r][0] - CX, fpos[r][1] - CY)}' for r in refs) or '—'
            print(f'| {k} | `{net.name}` | {n} | {far} |')
        print(f'\nWrite the groups to {Path(a.board_dir) / "fanout" / (a.ref + ".json")}:\n'
              '{"groups": [{"name": "ADC", "dir": "W", "nets": ["/ADC_SDIO", ...]}, ...]}')
        return

    R = max(CXR - CX, CX - CXL, CYB - CY, CY - CYT) + 4
    inR = lambda p: abs(p[0] - CX) < R and abs(p[1] - CY) < R

    def is_strap(t):
        """A power strap: a track whose two ends sit on adjacent balls of its net."""
        s0, s1 = R3(P(t.start)), R3(P(t.end))
        if not POWER.match(t.net.name) or not ballnet.get(s0) == ballnet.get(s1) == t.net.name:
            return False
        d = math.hypot(s1[0] - s0[0], s1[1] - s0[1])
        return abs(d - pitch) < 0.01 or abs(d - pitch * math.sqrt(2)) < 0.01

    # ---------------------------------------------------------------- straps
    if a.step == 'straps':
        old = [t for t in tracks if t.layer in (LF, L2) and is_strap(t)]
        oldid = {t.id.value for t in old}
        vall = [(*P(v.position), v.net.name, max(v.padstack.copper_layer(LF).size.x / NM / 2 + CL, VH / 2 + HCL))
                for v in allv if inR(P(v.position))]
        segs = [(s, e, t.width / NM, t.net.name) for t in tracks if t.layer == LF and t.id.value not in oldid
                for s, e in segs_of(t) if inR(s) or inR(e)]
        fpads = [(*P(p.position), p.net.name, pad_r(p, LF)) for p in b.get_pads()
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
        straps, blocked_pairs = [], 0
        for kind, n1, n2, net, p0, p1 in sorted(cand, key=lambda c: c[0]):
            if kind == 1 and root(n1) == root(n2):
                continue
            if fits(p0, p1, net.name):
                straps.append([p0, p1, TW, net]); segs.append((p0, p1, TW, net.name)); par[root(n1)] = root(n2)
            else:
                blocked_pairs += 1
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
        print(f'straps: remove {len(old)}, draw {len(straps)} on F.Cu; '
              f'{blocked_pairs} pairs without clearance, not strapped')
        write(old, [mk_track(LF, p0, p1, w, net) for p0, p1, w, net in straps])
        return

    # ---------------------------------------------------------------- route
    gfile = Path(a.board_dir) / 'fanout' / f'{a.ref}.json'
    if not gfile.exists():
        raise Bad(f"no {gfile}: run the groups step and write it")
    groups = {g['name']: g for g in json.loads(gfile.read_text())['groups']}
    if a.group not in groups:
        raise Bad(f"no group {a.group} in {gfile}: {', '.join(groups)}")
    grp = groups[a.group]
    D = grp['dir'].upper()
    if D not in DIRS:
        raise Bad(f"group {a.group}: dir {D} is not one of {' '.join(DIRS)}")
    dvx, dvy = DIRS[D]
    gnets = set(grp['nets'])
    mine = [bl for bl in sig if bl[1].name in gnets]
    missing = gnets - {bl[1].name for bl in mine}
    if missing:
        raise Bad(f"group {a.group}: no signal ball of {a.ref} on {', '.join(sorted(missing))}")
    vpos = {R3(P(v.position)) for v in allv}
    if any(R3((x, y)) not in vpos for _, _, x, y in mine):
        raise Bad("a ball of the group has no via: run the vias step first")

    # the group's own earlier fan-out: its tracks on F.Cu and In2.Cu, its end vias, their GND vias
    old_t = [t for t in tracks if t.net.name in gnets and t.layer in (LF, L2)
             and inR(P(t.start)) and inR(P(t.end))]
    old_v = [v for v in allv if v.net.name in gnets and R3(P(v.position)) not in ballpos and inR(P(v.position))]
    ev = [P(v.position) for v in old_v]
    old_g = [v for v in allv if isg(v.net.name) and R3(P(v.position)) not in ballpos
             and any(abs(math.dist(P(v.position), q) - VV) < 0.005 for q in ev)]
    old = old_t + old_v + old_g
    oldid = {o.id.value for o in old}

    # fixed copper per layer: (a, b, copper radius, net, kind)
    LAY = [LF, L2]
    obs = {L: [] for L in LAY}
    for t in tracks:
        if t.id.value in oldid or t.layer not in LAY:
            continue
        for s, e in segs_of(t):
            if inR(s) or inR(e):
                obs[t.layer].append((s, e, t.width / NM / 2, t.net.name, 'trk'))
    for v in allv:
        p = P(v.position)
        if v.id.value in oldid or not inR(p):
            continue
        r = v.padstack.copper_layer(LF).size.x / NM / 2
        for L in LAY:
            obs[L].append((p, p, r, v.net.name, 'via'))
    for p in b.get_pads():
        pp = P(p.position)
        if inR(pp):
            for L in LAY:
                if L in p.padstack.layers:
                    obs[L].append((pp, pp, pad_r(p, L), p.net.name, 'pad'))

    X0, Y0 = CX - R, CY - R
    S = int(round(2 * R / G)) + 1
    xy = lambda i, j: (X0 + i * G, Y0 + j * G)
    ij = lambda x, y: (int(round((x - X0) / G)), int(round((y - Y0) / G)))

    def disk(p, r):
        i0, j0 = ij(p[0] - r, p[1] - r); i1, j1 = ij(p[0] + r, p[1] + r)
        return [(i, j) for i in range(max(0, i0 - 1), min(S, i1 + 2)) for j in range(max(0, j0 - 1), min(S, j1 + 2))
                if math.hypot(xy(i, j)[0] - p[0], xy(i, j)[1] - p[1]) < r]

    def capsule(s, e, r):
        i0, j0 = ij(min(s[0], e[0]) - r, min(s[1], e[1]) - r)
        i1, j1 = ij(max(s[0], e[0]) + r, max(s[1], e[1]) + r)
        return [(i, j) for i in range(max(0, i0 - 1), min(S, i1 + 2)) for j in range(max(0, j0 - 1), min(S, j1 + 2))
                if pd(*xy(i, j), *s, *e) < r]

    fixed = {L: defaultdict(set) for L in LAY}          # cell -> nets whose copper keeps a trace centre out
    for L in LAY:
        for s, e, r, nn, kind in obs[L]:
            rr = r + CL + TW / 2 + M
            if kind == 'via':
                rr = max(rr, VH / 2 + HCL + TW / 2 + M)
            for c in capsule(s, e, rr):
                fixed[L][c].add(nn)

    def via_clear(p, net):
        """A through via at p clears other nets' fixed copper on both layers."""
        for L in LAY:
            for s, e, r, nn, kind in obs[L]:
                if nn == net:
                    continue
                need = r + VR if kind != 'via' else max(r + VD / 2 + CL, VH + HH)
                if pd(*p, *s, *e) < need - 1e-6:
                    return False
        return True

    # end-via sites: on the courtyard edge of each side the direction faces, then
    # columns 0.8 mm further out, rows 0.8 mm apart, alternate columns offset 0.4.
    # The GND via sits 0.40 mm from the end via toward the part, on the edge normal.
    edges = []
    if dvx < 0: edges.append(('W', CXL, (-1, 0)))
    if dvx > 0: edges.append(('E', CXR, (1, 0)))
    if dvy < 0: edges.append(('N', CYT, (0, -1)))
    if dvy > 0: edges.append(('S', CYB, (0, 1)))
    sites = []
    for name, pos, (nx, ny) in edges:
        for k in range(4):
            off = pos + (nx + ny) * 0.8 * k
            lo, hi = (CYT, CYB) if nx else (CXL, CXR)
            t = lo + 0.4 * (k % 2)
            while t <= hi + 1e-6:
                p = (off, t) if nx else (t, off)
                g = (p[0] - nx * VV, p[1] - ny * VV)
                sites.append((R3(p), R3(g), name))
                t += 0.8
    usable = {}
    for st in sites:
        if via_clear(st[1], GND.name):
            ok = [bl[1].name for bl in mine if via_clear(st[0], bl[1].name)]
            if ok:
                usable[st] = set(ok)
    print(f'group {a.group}: {len(mine)} nets, direction {D}; {len(usable)} end-via sites')

    R_VIA = VR + TW / 2 + M
    R_TRK = TW + CL + M
    D_TRK = [(di, dj) for di in range(-3, 4) for dj in range(-3, 4) if math.hypot(di, dj) * G < R_TRK]
    D_VIA = [(di, dj) for di in range(-4, 5) for dj in range(-4, 5) if math.hypot(di, dj) * G < R_VIA]
    nid = {bl[1].name: k + 1 for k, bl in enumerate(mine)}
    GID = -1
    D8 = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    straight = dvx == 0 or dvy == 0
    # never back; across the direction only on a straight side
    MOVES = [k for k, (di, dj) in enumerate(D8) if di * dvx + dj * dvy > 0 or (straight and di * dvx + dj * dvy == 0)]

    goal_of = defaultdict(list)       # site cell -> sites
    for st in usable:
        goal_of[ij(*st[0])].append(st)
    gnd_disk = {st: set(disk(st[1], R_VIA)) for st in usable}

    occ = {L: defaultdict(Counter) for L in LAY}
    hist = {L: defaultdict(float) for L in LAY}
    route = {}

    def footprint(net, r):
        L, cells, st = r
        k = nid[net]
        out = [(L, (c[0] + di, c[1] + dj), k) for c in cells for di, dj in D_TRK]
        ce, cg = ij(*st[0]), ij(*st[1])
        for LL in LAY:
            out += [(LL, (ce[0] + di, ce[1] + dj), k) for di, dj in D_VIA]
            out += [(LL, (cg[0] + di, cg[1] + dj), GID) for di, dj in D_VIA]
        return out

    def add(net, r, sign):
        for L, c, k in footprint(net, r):
            occ[L][c][k] += sign
            if occ[L][c][k] == 0:
                del occ[L][c][k]

    def astar(net, L, start, pres):
        k = nid[net]
        bx, by = start
        band = int(round(a.band / G))
        off_band = lambda c: straight and ((dvy and abs(c[0] - bx) > band) or (dvx and abs(c[1] - by) > band))
        blocked = lambda c: bool(fixed[L].get(c, set()) - {net})

        def cost(c):
            o = occ[L].get(c)
            n_o = sum(v for kk, v in o.items() if kk != k) if o else 0
            return (1 + hist[L][c]) * (1 + pres * n_o)

        goals = {c for c, sts in goal_of.items() if any(net in usable[s] for s in sts) and not off_band(c)}
        if not goals:
            return None
        gl = list(goals)
        h = lambda c: min(abs(c[0] - g[0]) + abs(c[1] - g[1]) for g in gl) * 0.9
        pq = [(h(start), 0.0, start, -1)]
        best = {(start, -1): 0.0}
        prev = {}
        while pq:
            f, g, c, d = heapq.heappop(pq)
            if g > best.get((c, d), 1e18):
                continue
            if c in goals and c != start:
                path = [c]; st_ = (c, d)
                while st_ in prev:
                    st_ = prev[st_]; path.append(st_[0])
                path = path[::-1]
                cells = set(path)
                for st in goal_of[c]:
                    if net in usable[st] and not (cells & gnd_disk[st]):
                        return path, st, g
            for nd in MOVES:
                di, dj = D8[nd]
                n = (c[0] + di, c[1] + dj)
                if not (0 <= n[0] < S and 0 <= n[1] < S) or blocked(n) or off_band(n):
                    continue
                if di and dj and (blocked((c[0] + di, c[1])) or blocked((c[0], c[1] + dj))):
                    continue
                across = di * dvx + dj * dvy == 0
                step = (1.4142 if di and dj else 1) * (1.5 if across else 1)
                g2 = g + step * cost(n) + (2 if d != -1 and nd != d else 0)
                if g2 < best.get((n, nd), 1e18):
                    best[(n, nd)] = g2; prev[(n, nd)] = (c, d)
                    heapq.heappush(pq, (g2 + h(n), g2, n, nd))
        return None

    order = sorted(mine, key=lambda bl: -((bl[2] - CX) * dvx + (bl[3] - CY) * dvy))   # nearest the edge first
    pres = 0.5
    done = False
    conflicted, unrouted = set(), []
    for it in range(a.iters):
        t0 = time.time()
        for bl in order:
            net = bl[1].name
            if net in route:
                add(net, route[net], -1)
                del route[net]
            start = ij(bl[2], bl[3])
            best = None
            for L in LAY:
                r = astar(net, L, start, pres)
                if r and (best is None or r[2] < best[3]):
                    best = (L, *r)
            if best:
                L, path, st, _ = best
                route[net] = (L, path, st)
                add(net, route[net], +1)
        # a net's centre line in another's footprint; two nets' vias closer than the via spacing
        conflicted = set()
        for net, r in route.items():
            k = nid[net]
            for c in r[1]:
                if any(x != k for x in occ[r[0]].get(c, {})):
                    conflicted.add(net); break
        for L in LAY:
            for c, o in occ[L].items():
                if len(o) > 1:
                    hist[L][c] += 1
        vias_of = [(n, p) for n, r in route.items() for p in r[2][:2]]
        for i, (n1, p1) in enumerate(vias_of):
            for n2, p2 in vias_of[i + 1:]:
                if n1 != n2 and math.dist(p1, p2) < VV - 1e-6:
                    conflicted |= {n1, n2}
        unrouted = [bl[1].name for bl in mine if bl[1].name not in route]
        print(f'  pass {it + 1}: conflicts {len(conflicted)}, unrouted {len(unrouted)}, {time.time() - t0:.0f} s')
        if not conflicted and not unrouted:
            done = True
            break
        pres *= 1.6
        cs = conflicted | set(unrouted)
        order = [bl for bl in order if bl[1].name in cs] + [bl for bl in order if bl[1].name not in cs]
    if not done:
        raise Bad(f'group {a.group}: no conflict-free fan-out toward {D} within {a.band} mm of drift; '
                  f'unrouted {", ".join(unrouted) or "none"}; in conflict {", ".join(sorted(conflicted)) or "none"}')

    def corners(p):
        pts = [p[0]]
        for a_, c, d in zip(p, p[1:], p[2:]):
            if (c[0] - a_[0], c[1] - a_[1]) != (d[0] - c[0], d[1] - c[1]):
                pts.append(c)
        pts.append(p[-1])
        return pts

    create, report = [], []
    for bl in mine:
        net = bl[1].name
        L, path, st = route[net]
        pts = [xy(*c) for c in corners(path)]
        pts[0] = (bl[2], bl[3]); pts[-1] = st[0]
        create += [mk_track(L, p0, p1, TW, netobj[net]) for p0, p1 in zip(pts, pts[1:]) if p0 != p1]
        create += [mk_via(*st[0], netobj[net]), mk_via(*st[1], GND)]
        drift = max(abs(x - bl[2]) if dvy and not dvx else abs(y - bl[3]) if dvx and not dvy else 0 for x, y in pts)
        report.append((net, bl[0], LNAME[L], st[0], drift))
    print(f'\n| # | Net | Ball | Layer | End via, from centre | Drift |\n|---|---|---|---|---|---|')
    for k, (net, n, ln, p, dr) in enumerate(sorted(report), 1):
        print(f'| {k} | `{net}` | {n} | {ln} | {p[0] - CX:+.2f}, {p[1] - CY:+.2f} | {dr:.2f} |')
    print(f'\nremove {len(old_t)} tracks, {len(old_v)} end vias, {len(old_g)} GND vias; '
          f'create {sum(1 for c in create if isinstance(c, Track))} tracks, {2 * len(mine)} vias')
    write(old, create)


if __name__ == "__main__":
    main()
