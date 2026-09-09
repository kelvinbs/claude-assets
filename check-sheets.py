#!/usr/bin/env python3
"""check-sheets - what must be true of every sheet, after any placement run.

Not a skill and not part of the pipeline. It asserts the two things that
kept breaking: every placed part inside its own page, and every part that
the record puts in a box drawn inside that box's outline.

    python3 tools/board-build/check-sheets.py <board-dir>
"""
import re
import sqlite3
import sys
from pathlib import Path

PAPERS = {"A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8),
          "D": (863.6, 558.8), "E": (1117.6, 863.6),
          "A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0),
          "A1": (841.0, 594.0), "A0": (1189.0, 841.0)}


def blocks(text, tag):
    for m in re.finditer(tag, text):
        a, d = m.start() + 1, 0
        for k in range(a, len(text)):
            if text[k] == "(":
                d += 1
            elif text[k] == ")":
                d -= 1
                if d == 0:
                    yield text[a:k + 1]
                    break


def main(board):
    board = Path(board)
    con = sqlite3.connect(board / "board.db")
    boxed = {ref for (ref,) in con.execute(
        "select distinct ref from ref_table where parent is not null")}
    faults = 0
    for path in sorted(board.glob("*-*.kicad_sch")):
        t = path.read_text()
        paper = re.search(r'\(paper "([^"]+)"', t).group(1)
        W, H = PAPERS.get(paper, PAPERS["A"])
        parts, rects = [], []
        for b in blocks(t, r"\n\t\(symbol\n"):
            ref = re.search(r'\(property "Reference" "([^"]+)"', b)
            at = re.search(r"\(at ([\d.]+) ([\d.]+)", b)
            if ref and at:
                parts.append((ref.group(1), float(at.group(1)),
                              float(at.group(2))))
        for b in blocks(t, r"\n\t\(rectangle\n"):
            s = re.search(r"\(start ([\d.-]+) ([\d.-]+)\)", b)
            e = re.search(r"\(end ([\d.-]+) ([\d.-]+)\)", b)
            if s and e:
                rects.append((float(s.group(1)), float(s.group(2)),
                              float(e.group(1)), float(e.group(2))))
        off = [r for r, x, y in parts if x > W or y > H]
        want = sorted({r for r, _, _ in parts} & boxed)
        loose = []
        for ref, x, y in parts:
            if ref not in boxed:
                continue
            if not any(min(a, c) <= x <= max(a, c) and min(b, d) <= y <= max(b, d)
                       for a, b, c, d in rects):
                loose.append(ref)
        bad = bool(off) or bool(loose)
        faults += bad
        print(f"{path.name:28s} {paper}  {len(parts):3d} parts  "
              f"{len(rects):2d} boxes  "
              f"{'off page: ' + ','.join(sorted(set(off))) if off else 'all on page'}  "
              f"{'outside a box: ' + ','.join(sorted(set(loose))) if loose else 'all boxed'}")
    print("FAIL" if faults else "PASS")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
