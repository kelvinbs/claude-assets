#!/usr/bin/env python3
"""zone-fill — refill every zone of a saved .kicad_pcb, headless, through
kicad-cli, and save it.

    zone-fill.py <board.kicad_pcb> [--dry-run]

pcbnew's zone filler crashes outside KiCad (board-read §4), so the fill is
kicad-cli's: `pcb drc --refill-zones --save-board`, run on a copy of the
board beside a copy of its project files. The board is replaced only when
that copy ends whole and kicad-cli loads it again; the DRC table compares
the board before with the board after. `--dry-run` fills the copy and
prints the table; nothing is written.

KiCad must not be running. The board is backed up first — board-read.py.
"""

import datetime
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "zone-fill"


def main():
    ap = br.edit_parser(TOOL, __doc__)
    a = ap.parse_args()
    board = br.board_arg(TOOL, a.board)
    br.guard(TOOL, board)
    stem = board.stem
    pro = board.with_suffix(".kicad_pro")
    dru = board.with_suffix(".kicad_dru")
    tmp = board.parent / f".{stem}.board-edit-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        before = board.stat()
        ref = br.stage_copy(board, pro, dru, tmp / "before", stem)
        out = br.stage_copy(board, pro, dru, tmp / "after", stem)
        run = subprocess.run([br.KICAD_CLI, "pcb", "drc", "--refill-zones",
                              "--save-board", "--format", "json", "-o",
                              str(tmp / "fill.json"), str(out)],
                             capture_output=True, text=True)
        if run.returncode != 0 or not br.whole(out):
            raise br.Bad(TOOL, "kicad-cli could not fill the copy: "
                         + (run.stderr or run.stdout).strip()[-400:]
                         + "; the board is unchanged")
        d0 = br.drc(TOOL, ref, tmp / "drc-before.json")
        d1 = br.drc(TOOL, out, tmp / "drc-after.json")
        keys = sorted(set(d0) | set(d1))
        rows = [[k, d0.get(k, 0), d1.get(k, 0), d1.get(k, 0) - d0.get(k, 0)]
                for k in keys if d0.get(k, 0) != d1.get(k, 0)]
        rows.append(["total", sum(d0.values()), sum(d1.values()),
                     sum(d1.values()) - sum(d0.values())])
        print(br.table(["DRC", "before", "after", "change"], rows,
                       "DRC, kicad-cli, before and after the refill"))
        if a.dry_run:
            print(f"{TOOL}: dry run; nothing written")
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = board.with_name(f"{board.name}.bak-{stamp}")
        shutil.copy2(board, bak)
        now = board.stat()
        if (now.st_mtime_ns, now.st_size) != (before.st_mtime_ns,
                                              before.st_size):
            raise br.Bad(TOOL, "the board changed on disk during the run; "
                         "nothing replaced")
        os.replace(out, board)
        print(f"{TOOL}: zones refilled and saved to {board.name}; "
              f"back-up {bak.name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except br.Bad as e:
        sys.exit(str(e))
