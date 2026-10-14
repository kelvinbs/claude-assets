#!/usr/bin/env python3
"""bga-fanout — fan out a ball grid array on the board open in KiCad.

    bga-fanout.py <board-dir> <ref> [options]

A via in pad on every connected ball. Every signal ends on the signal
layer on the fence line, the courtyard by default. A signal may run on the
escape layer first and change layer at a via inside the courtyard; each
such via has a GND via at the minimum via spacing, on the normal to the
nearest courtyard edge, on its inner side. Signals negotiate grid cells on
both layers (PathFinder): overlaps cost more each pass until none remain.
A step inward costs extra, so traces head out. Power balls of one net are
strapped on F.Cu where clearance allows and on no other layer, widened
up to 0.5 mm; the power planes are the User's.
--straps-only redraws the straps and nothing else.

One read of the board, one remove, one create. Saved only with --save.
"""

import argparse
import heapq
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


def main():
    ensure_kipy()
    retry_busy()
    from kipy import KiCad
    from kipy.board_types import BoardLayer, PadStackShape, Track, Via
    from kipy.geometry import Vector2

    ap = argparse.ArgumentParser()
    ap.add_argument('board_dir'); ap.add_argument('ref')
    ap.add_argument('--signal-layer', default='In2.Cu')
    ap.add_argument('--escape-layer', default='In4.Cu')
    ap.add_argument('--straps-only', action='store_true', help='redraw the power straps only')
    ap.add_argument('--fence', type=float, help='mm from centre; default the courtyard')
    ap.add_argument('--fence-max', type=float, help='mm; default the fence')
    ap.add_argument('--via', type=float, default=0.25)
    ap.add_argument('--drill', type=float, default=0.15)
    ap.add_argument('--track', type=float, default=0.11)
    ap.add_argument('--clearance', type=float, default=0.10)
    ap.add_argument('--hole-clearance', type=float, default=0.20)
    ap.add_argument('--hole-to-hole', type=float, default=0.22)
    ap.add_argument('--power', default=r'^/?\+?\d+V\d*$', help='power net names, regex')
    ap.add_argument('--iters', type=int, default=40)
    ap.add_argument('--inward', type=float, default=4, help='extra cost of a step inward')
    ap.add_argument('--dry-run', action='store_true'); ap.add_argument('--save', action='store_true')
    a = ap.parse_args()
    if not Path(a.board_dir).is_dir():
        raise Bad(f"no folder {a.board_dir}")

    def layer(name):
        try:
            return getattr(BoardLayer, 'BL_' + name.replace('.', '_'))
        except AttributeError:
            raise Bad(f"no layer {name}")

    VD, VH, TW, CL, HCL, HH, M = a.via, a.drill, a.track, a.clearance, a.hole_clearance, a.hole_to_hole, 0.01
    VR = max(VD / 2 + CL, VH / 2 + HCL)          # via centre to other copper edge
    R_VIA = VR + TW / 2 + M                     # via centre to other trace centre line
    R_TRK = TW + CL + M                         # trace centre to other trace centre
    VV = round(max(VR + VD / 2, VH + HH), 3)    # via centre to via centre: the minimum, 0.40
    VH2 = VH + HH                               # via centre to via centre, same net: hole to hole
    G = 0.1
    LS, LE, LT = layer(a.signal_layer), layer(a.escape_layer), BoardLayer.BL_F_Cu
    POWER = re.compile(a.power)

    try:
        b = KiCad(timeout_ms=10000).get_board()
    except Exception as e:
        raise Bad(f"no board open in KiCad: {e}")
    # KiCad 10 segfaults in the Schematic Editor's API handler,
    # checkForBusy(), on a board delete while the Schematic Editor is open
    # (radar_2 crash-incidents 1.3, 1.4). Its window says it is open.
    if sch_open():
        close_schematic()
        if sch_open():
            raise Bad("the Schematic Editor is still open after save and "
                      "close: a board edit with it open crashes KiCad")
        print("Schematic Editor saved and closed")
    fps = [f for f in b.get_footprints() if f.reference_field.text.value == a.ref]
    if not fps:
        raise Bad(f"no footprint {a.ref} on the open board")
    fp = fps[0]
    pads = list(fp.definition.pads)
    CX, CY = fp.position.x / NM, fp.position.y / NM
    xs = sorted({round(p.position.x / NM, 3) for p in pads})
    pitch = min(x2 - x1 for x1, x2 in zip(xs, xs[1:]) if x2 - x1 > 0.05)
    span = max(max(abs(p.position.x / NM - CX), abs(p.position.y / NM - CY)) for p in pads)
    RING_IN = span + pitch / 2
    cy = [s for s in fp.definition.shapes if s.layer == BoardLayer.BL_F_CrtYd]
    if a.fence is None:
        if not cy:
            raise Bad(f"{a.ref} has no courtyard on F.Courtyard; give --fence")
        bb = b.get_item_bounding_box(cy)[0]
        a.fence = round(min(CX - bb.pos.x / NM, bb.pos.x / NM + bb.size.x / NM - CX,
                            CY - bb.pos.y / NM, bb.pos.y / NM + bb.size.y / NM - CY), 1)
    if a.fence_max is None:
        a.fence_max = a.fence
    isg = lambda n: n.upper().endswith('GND')
    balls = [(p.number, p.net, p.position.x / NM, p.position.y / NM) for p in pads
             if p.net.name and 'unconnected' not in p.net.name]
    ballpos = {(round(x, 3), round(y, 3)) for _, _, x, y in balls}
    sig = [bl for bl in balls if not isg(bl[1].name) and not POWER.match(bl[1].name)]
    pwr = [bl for bl in balls if POWER.match(bl[1].name)]
    if not any(isg(bl[1].name) for bl in balls):
        raise Bad(f"{a.ref} has no GND ball")
    GND = next(bl[1] for bl in balls if isg(bl[1].name))
    netobj = {bl[1].name: bl[1] for bl in balls}
    print(f'{a.ref}: {len(sig)} signals, {len(pwr)} power balls, pitch {pitch}')

    # ---- wipe set and what stays
    R = a.fence_max + 4
    inR = lambda x, y: abs(x - CX) < R and abs(y - CY) < R
    nets = {bl[1].name for bl in balls}
    signets = {bl[1].name for bl in sig}
    ballnet = {(round(x, 3), round(y, 3)): net.name for _, net, x, y in balls}

    def is_strap(t):
        """A power strap: a track whose two ends sit on adjacent balls of its net."""
        s0 = (round(t.start.x / NM, 3), round(t.start.y / NM, 3))
        s1 = (round(t.end.x / NM, 3), round(t.end.y / NM, 3))
        if not POWER.match(t.net.name) or not ballnet.get(s0) == ballnet.get(s1) == t.net.name:
            return False
        d = math.hypot(s1[0] - s0[0], s1[1] - s0[1])
        return abs(d - pitch) < 0.01 or abs(d - pitch * math.sqrt(2)) < 0.01

    old_s = [t for t in b.get_tracks() if t.layer in (LS, LT) and is_strap(t)]
    if a.straps_only:
        old_t, old_v = old_s, []
    else:
        old_t = [t for t in b.get_tracks() if t.layer in (LS, LE) and t.net.name in nets
                 and inR(t.start.x / NM, t.start.y / NM) and inR(t.end.x / NM, t.end.y / NM)]
        old_t += [t for t in old_s if t.id.value not in {o.id.value for o in old_t}]
        old_v = [v for v in b.get_vias()
                 if (round(v.position.x / NM, 3), round(v.position.y / NM, 3)) in ballpos
                 or (v.net.name in signets and inR(v.position.x / NM, v.position.y / NM))
                 or (isg(v.net.name) and span + pitch / 4 < max(abs(v.position.x / NM - CX), abs(v.position.y / NM - CY)) < R)]
    OLD = {o.id.value for o in old_t + old_v}
    keep_t = [t for t in b.get_tracks() if t.id.value not in OLD and (inR(t.start.x / NM, t.start.y / NM) or inR(t.end.x / NM, t.end.y / NM))]
    keep_v = [v for v in b.get_vias() if v.id.value not in OLD and inR(v.position.x / NM, v.position.y / NM)]
    other_pads = [p for p in b.get_pads() if inR(p.position.x / NM, p.position.y / NM)]


    def padr(p):
        for ly in (BoardLayer.BL_F_Cu, BoardLayer.BL_B_Cu):
            try:
                s = p.padstack.copper_layer(ly).size
                if s.x:
                    return math.hypot(s.x, s.y) / 2 / NM
            except Exception:
                pass
        return 1.0


    def pd(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        u = 0 if ll == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / ll))
        return math.hypot(px - ax - u * dx, py - ay - u * dy)


    fixed_v = [(x, y, net.name, VD / 2) for _, net, x, y in balls] + \
              [(v.position.x / NM, v.position.y / NM, v.net.name, v.padstack.copper_layer(BoardLayer.BL_F_Cu).size.x / NM / 2) for v in keep_v]
    fixed_t = [((t.start.x / NM, t.start.y / NM), (t.end.x / NM, t.end.y / NM), t.width / NM, t.net.name, t.layer) for t in keep_t]


    def via_clear(x, y, net):
        """A through via at x, y clears fixed copper on every layer."""
        for s0, s1, w, nn, ly in fixed_t:
            if nn != net and pd(x, y, *s0, *s1) < VR + w / 2:
                return False
        for vx, vy, nn, vr in fixed_v:
            if math.hypot(vx - x, vy - y) < (VH2 if nn == net else VR + vr) - 1e-6:
                return False
        for p in other_pads:
            if p.net.name != net and math.hypot(p.position.x / NM - x, p.position.y / NM - y) < VR + padr(p):
                return False
        return True


    def solve(FENCE):
        INW = a.inward
        N = int(round(FENCE / G)); S = 2 * N + 1
        idx = lambda x, y: (int(round((x - CX) / G)) + N, int(round((y - CY) / G)) + N)
        xy = lambda i, j: ((i - N) * G + CX, (j - N) * G + CY)
        nid = {n: k + 1 for k, n in enumerate(sorted(nets | {GND.name}))}
        GID = nid[GND.name]

        def disk(r):
            rr = int(r / G) + 1
            return [(di, dj) for di in range(-rr, rr + 1) for dj in range(-rr, rr + 1) if math.hypot(di, dj) * G < r]
        D_VIA, D_TRK = disk(R_VIA), disk(R_TRK)

        # fixed: per layer, cell -> set of nets whose fixed copper blocks it for others
        fixed = [defaultdict(set), defaultdict(set)]
        for L in (0, 1):
            for x, y, nn, vr in fixed_v:
                i0, j0 = idx(x, y)
                for di, dj in disk(max(vr + CL, VH / 2 + HCL) + TW / 2 + M):
                    fixed[L][(i0 + di, j0 + dj)].add(nid.get(nn, -2))
            ly = LS if L == 0 else LE
            for s0, s1, w, nn, l2 in fixed_t:
                if l2 != ly:
                    continue
                n = int(math.hypot(s1[0] - s0[0], s1[1] - s0[1]) / (G / 2)) + 1
                for t in range(n + 1):
                    i0, j0 = idx(s0[0] + (s1[0] - s0[0]) * t / n, s0[1] + (s1[1] - s0[1]) * t / n)
                    for di, dj in disk(TW / 2 + w / 2 + CL + M):
                        fixed[L][(i0 + di, j0 + dj)].add(nid.get(nn, -2))

        def blocked(L, c, k):
            s = fixed[L].get(c)
            return bool(s) and (s - {k})

        # transition sites: via on a ball line in the ring, GND via on the inner side, normal to the courtyard edge
        sites = []
        for i in range(S):
            for j in range(S):
                x, y = xy(i, j)
                r = max(abs(x - CX), abs(y - CY))
                if not (RING_IN <= r <= FENCE - VD / 2):
                    continue
                if abs(abs(x - CX) - abs(y - CY)) < 1e-6:
                    continue
                horiz = abs(x - CX) > abs(y - CY)
                tang = (y - CY) if horiz else (x - CX)
                q = (tang - pitch / 2) / pitch
                if abs(2 * q - round(2 * q)) > 0.04:
                    continue
                if not via_clear(x, y, None):
                    continue
                sn = math.copysign(1, (x - CX) if horiz else (y - CY))
                # GND via at the minimum via spacing, on the normal to the courtyard edge, on the inner side
                gx, gy = (x - sn * VV, y) if horiz else (x, y - sn * VV)
                gl = [(gx, gy)] if via_clear(gx, gy, GND.name) else []
                nrm = (sn, 0) if horiz else (0, sn)
                if gl:
                    sites.append(((i, j), (x, y), gl, nrm))
        site_at = {s[0]: s for s in sites}
        print(f'  fence {FENCE}: {len(sites)} via sites with a GND place')

        # negotiation state
        occ = [defaultdict(Counter), defaultdict(Counter)]     # cell -> nets' footprints
        hist = [defaultdict(float), defaultdict(float)]
        route = {}            # net -> (cells L3, cells L5, site, gnd)
        used_site = {}        # site cell -> net
        gnd_of = {}           # net -> gnd xy

        def footprint(net, r):
            cells3, cells5, site, g = r
            k = nid[net]
            out = []
            for L, cells in ((0, cells3), (1, cells5)):
                for c in cells:
                    for di, dj in D_TRK:
                        out.append((L, (c[0] + di, c[1] + dj), k))
            if site:
                for L in (0, 1):
                    for di, dj in D_VIA:
                        out.append((L, (site[0] + di, site[1] + dj), k))
                gi, gj = idx(*g)
                for L in (0, 1):
                    for di, dj in D_VIA:
                        out.append((L, (gi + di, gj + dj), GID))
            return out

        def add(net, r, sign):
            for L, c, k in footprint(net, r):
                occ[L][c][k] += sign
                if occ[L][c][k] == 0:
                    del occ[L][c][k]

        def gnd_ok(g, net):
            for n2, g2 in gnd_of.items():
                if n2 != net and math.hypot(g2[0] - g[0], g2[1] - g[1]) < VV:
                    return False
            for n2, r2 in route.items():
                if n2 != net and r2[2] and math.hypot(xy(*r2[2])[0] - g[0], xy(*r2[2])[1] - g[1]) < VV:
                    return False
            return True

        def site_free(s, net):
            x, y = xy(*s)
            for n2, r2 in route.items():
                if n2 == net:
                    continue
                if r2[2] and math.hypot(xy(*r2[2])[0] - x, xy(*r2[2])[1] - y) < VV:
                    return False
                if n2 in gnd_of and math.hypot(gnd_of[n2][0] - x, gnd_of[n2][1] - y) < VV:
                    return False
            return True

        D8 = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]

        def astar(net, start, pres):
            k = nid[net]

            def cost(L, c):
                o = occ[L].get(c)
                n_o = sum(v for kk, v in o.items() if kk != k) if o else 0
                return (1 + hist[L][c]) * (1 + pres * n_o)

            def h(L, i, j):
                e = min(i, S - 1 - i, j, S - 1 - j)
                return e + (3 if L == 1 else 0)
            si, sj = start
            pq = []
            best = {}
            prev = {}
            for L in (0, 1):
                st = (L, si, sj, -1)
                best[st] = 0.0
                heapq.heappush(pq, (h(L, si, sj), 0.0, st))
            while pq:
                f, g, st = heapq.heappop(pq)
                if g > best.get(st, 1e18):
                    continue
                L, i, j, d = st
                if L == 0 and (i in (0, S - 1) or j in (0, S - 1)):
                    path = [st]
                    while path[-1] in prev:
                        path.append(prev[path[-1]])
                    return path[::-1]
                # layer change L5 -> L3 at a site
                if L == 1 and (i, j) in site_at and (i, j) != start and site_free((i, j), net) and \
                        (d == -1 or D8[d][0] * site_at[(i, j)][3][0] + D8[d][1] * site_at[(i, j)][3][1] <= 0):
                    nst = (0, i, j, -1)
                    c2 = g + 6
                    if c2 < best.get(nst, 1e18):
                        best[nst] = c2; prev[nst] = st
                        heapq.heappush(pq, (c2 + h(0, i, j), c2, nst))
                for nd, (di, dj) in enumerate(D8):
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < S and 0 <= nj < S) or blocked(L, (ni, nj), k):
                        continue
                    if di and dj and (blocked(L, (i + di, j), k) or blocked(L, (i, j + dj), k)):
                        continue
                    step = (1.4142 if di and dj else 1) * (1.15 if L == 1 else 1)
                    inward = max(abs(ni - N), abs(nj - N)) < max(abs(i - N), abs(j - N))
                    c2 = g + step * cost(L, (ni, nj)) + (2 if d != -1 and nd != d else 0) + (INW if inward else 0)
                    nst = (L, ni, nj, nd)
                    if c2 < best.get(nst, 1e18):
                        best[nst] = c2; prev[nst] = st
                        heapq.heappush(pq, (c2 + h(L, ni, nj), c2, nst))
            return None

        def to_route(net, path):
            cells3, cells5, site = [], [], None
            for k_, (L, i, j, d) in enumerate(path):
                (cells3 if L == 0 else cells5).append((i, j))
                if k_ and path[k_ - 1][0] == 1 and L == 0:
                    site = (i, j)
            g = None
            if site:
                g = site_at[site][2][0]
            return (cells3, cells5, site, g)

        order = sorted(sig, key=lambda bl: min(span - abs(bl[2] - CX), span - abs(bl[3] - CY)))
        pres = 0.5
        for it in range(a.iters):
            t0 = time.time()
            for bl in order:
                net = bl[1].name
                if net in route:
                    add(net, route[net], -1)
                    gnd_of.pop(net, None)
                    del route[net]
                p = astar(net, idx(bl[2], bl[3]), pres)
                if p is None:
                    continue
                r = to_route(net, p)
                route[net] = r
                if r[2]:
                    gnd_of[net] = r[3]
                add(net, r, +1)
            # conflicts
            bad = set()
            for L in (0, 1):
                for c, o in occ[L].items():
                    if len(o) > 1:
                        hist[L][c] += 1
                        # a net's centre line in someone else's footprint
                        for net, r in route.items():
                            pass
            conflicted = set()
            for net, r in route.items():
                k = nid[net]
                for L, cells in ((0, r[0]), (1, r[1])):
                    for c in cells:
                        o = occ[L].get(c, {})
                        if any(kk != k for kk in o):
                            conflicted.add(net); break
            # GND via clashes
            gl = list(gnd_of.items())
            for i1 in range(len(gl)):
                for i2 in range(i1 + 1, len(gl)):
                    if math.hypot(gl[i1][1][0] - gl[i2][1][0], gl[i1][1][1] - gl[i2][1][1]) < VV:
                        conflicted.add(gl[i1][0]); conflicted.add(gl[i2][0])
            missing = [bl[1].name for bl in sig if bl[1].name not in route]
            n5 = sum(1 for r in route.values() if r[2])
            print(f'  pass {it + 1}: conflicts {len(conflicted)}, unrouted {len(missing)}, on L5 {n5}, {time.time() - t0:.0f} s')
            if not conflicted and not missing:
                return route, idx, xy, N
            pres *= 1.6
            # reroute conflicted first next pass
            cs = set(conflicted) | set(missing)
            order = [bl for bl in order if bl[1].name in cs] + [bl for bl in order if bl[1].name not in cs]
        return None, idx, xy, N


    if a.straps_only:
        route = {}
    else:
        fence = a.fence
        while True:
            route, idx, xy, N = solve(fence)
            if route or fence >= a.fence_max:
                break
            fence = round(fence + pitch / 2, 3)
        if not route:
            raise Bad(f'no conflict-free fan-out with the fence up to {a.fence_max} mm')
        print(f'fence {fence}: all {len(route)} signals fanned out')
        chb = {bl[1].name: max(abs(bl[2] - CX), abs(bl[3] - CY)) for bl in sig}
        inw = sorted(((round(chb[n] - min(max(abs(xy(*c)[0] - CX), abs(xy(*c)[1] - CY)) for c in r[0] + r[1]), 2), n) for n, r in route.items()), reverse=True)
        print('inward excursions over 0.3:', [w for w in inw if w[0] > 0.3])


    def corners(p):
        pts = [p[0]]
        for a_, c, d in zip(p, p[1:], p[2:]):
            if (c[0] - a_[0], c[1] - a_[1]) != (d[0] - c[0], d[1] - c[1]):
                pts.append(c)
        pts.append(p[-1])
        return pts


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


    tracks, vias = [], [] if a.straps_only else [mk_via(x, y, net) for _, net, x, y in balls]
    segs3 = []
    for net, (c3, c5, site, g) in route.items():
        for cells, ly in ((c3, LS), (c5, LE)):
            if len(cells) < 2:
                continue
            pts = [xy(*c) for c in corners(cells)]
            for p0, p1 in zip(pts, pts[1:]):
                if p0 != p1:
                    tracks.append(mk_track(ly, p0, p1, TW, netobj[net]))
                    if ly == LS:
                        segs3.append((p0, p1, TW, net))
        if site:
            vias.append(mk_via(*xy(*site), netobj[net]))
            vias.append(mk_via(*g, GND))

    # power straps: on F.Cu where clearance allows, on no other layer
    vall = [(x, y, net.name, VR) for _, net, x, y in balls] + \
           [(v.position.x / NM, v.position.y / NM, v.net.name, VR) for v in vias] + \
           [(x, y, nn, max(vr + CL, VH / 2 + HCL)) for x, y, nn, vr in fixed_v]


    def segd(p, q, r, s):
        o = lambda a_, b_, c: (b_[0] - a_[0]) * (c[1] - a_[1]) - (b_[1] - a_[1]) * (c[0] - a_[0])
        if o(p, q, r) * o(p, q, s) < 0 and o(r, s, p) * o(r, s, q) < 0:
            return 0
        return min(pd(*p, *r, *s), pd(*q, *r, *s), pd(*r, *p, *q), pd(*s, *p, *q))


    segs = {LS: segs3}
    segs.setdefault(LT, [])
    for t in keep_t:
        if t.layer in segs:
            segs[t.layer].append(((t.start.x / NM, t.start.y / NM), (t.end.x / NM, t.end.y / NM), t.width / NM, t.net.name))


    def pad_r(p, ly):
        cl = p.padstack.copper_layer(ly) or (p.padstack.copper_layers[0] if p.padstack.copper_layers else None)
        if cl is None or not cl.size.x:
            return 1.0
        if cl.shape == PadStackShape.PSS_CIRCLE:
            return max(cl.size.x, cl.size.y) / 2 / NM
        return math.hypot(cl.size.x, cl.size.y) / 2 / NM
    pads = {ly: [(p.position.x / NM, p.position.y / NM, p.net.name, pad_r(p, ly)) for p in other_pads if ly in p.padstack.layers]
            for ly in segs}
    par = {}


    def root(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x


    def fits(ly, p0, p1, net):
        return all(nn == net or pd(x, y, *p0, *p1) >= vr + TW / 2 for x, y, nn, vr in vall) and \
               all(nn == net or pd(x, y, *p0, *p1) >= r + CL + TW / 2 for x, y, nn, r in pads[ly]) and \
               all(nn == net or segd(p0, p1, s0, s1) >= TW / 2 + w / 2 + CL for s0, s1, w, nn in segs[ly])


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
        if fits(LT, p0, p1, net.name):
            straps.append([p0, p1, TW, net, LT]); segs[LT].append((p0, p1, TW, net.name)); par[root(n1)] = root(n2)
        else:
            blocked_pairs += 1
    for s in straps:
        lim = 0.25
        for s0, s1, w, nn in segs[s[4]]:
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
        for x, y, nn, r in pads[s[4]]:
            if nn != s[3].name:
                lim = min(lim, pd(x, y, *s[0], *s[1]) - r - CL)
        s[2] = max(TW, math.floor(2 * lim * 100) / 100)
    tracks += [mk_track(ly, p0, p1, w, net) for p0, p1, w, net, ly in straps]
    print(f'straps: {len(straps)} on F.Cu, {blocked_pairs} pairs without clearance, not strapped')
    print(f'wipe {len(old_t)} tracks {len(old_v)} vias; create {len(vias)} vias ({sum(1 for r in route.values() if r[2])} layer changes, as many GND), {len(tracks)} tracks, {len(straps)} power straps')
    if a.dry_run:
        return
    b.remove_items(old_t + old_v)
    if vias:
        b.create_items(vias)
    b.create_items(tracks)
    if a.save:
        b.save(); print('saved')


if __name__ == "__main__":
    main()
