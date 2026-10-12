"""Cut a filmed run into the parts worth keeping (cuo-gka): trim (one or several parts joined), speed up,
and put clips side by side. Writes H.264 MP4 at 30 fps and prints each output's length.

    python3 tools/video/cut.py IN.mp4 OUT.mp4 --part 12:40 [--part 95:130] [--speed 4]
    python3 tools/video/cut.py --grid OUT.mp4 A.mp4 B.mp4 [C.mp4 ...] [--width 1920]

Times are seconds or M:SS. film.py's clips already have the title bar cropped; for a raw .mov from
the recorder, --crop-top 56 takes it off (28 points at Retina scale).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from film import duration, mmss, side_by_side  # noqa: E402


def seconds(t: str) -> float:
    m, _, s = t.rpartition(":")
    return float(m or 0) * 60 + float(s)


def cut(src: Path, out: Path, parts: list[tuple[float, float]], speed: float, crop_top: int) -> bool:
    crop = f"crop=iw:ih-{crop_top}:0:{crop_top}," if crop_top else ""
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    for a, b in parts or [(0.0, duration(src))]:
        cmd += ["-ss", str(a), "-to", str(b), "-i", str(src)]
    n = max(1, len(parts))
    fc = "".join(f"[{i}:v]{crop}setpts=PTS-STARTPTS,fps=30[p{i}];" for i in range(n))
    fc += "".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[c];[c]setpts=PTS/{speed}[v]"
    r = subprocess.run(cmd + ["-filter_complex", fc, "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "medium",
                              "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="IN OUT, or with --grid: OUT then the clips")
    ap.add_argument("--part", action="append", default=[], help="FROM:TO to keep, e.g. 12:40 or 1:05:1:30 (repeatable)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--crop-top", type=int, default=0, help="pixels off the top (a raw recording's title bar)")
    ap.add_argument("--grid", action="store_true", help="the clips side by side, starting together")
    ap.add_argument("--width", type=int, default=1920)
    args = ap.parse_args()
    if args.grid:
        out, clips = args.files[0], args.files[1:]
        ok = len(clips) >= 2 and side_by_side(clips, out, args.width)
    else:
        if len(args.files) != 2:
            ap.error("give IN and OUT")
        src, out = args.files
        parts = []
        for p in args.part:
            bits = p.split(":")
            half = len(bits) // 2
            parts.append((seconds(":".join(bits[:half])), seconds(":".join(bits[half:]))))
        ok = cut(src, out, parts, args.speed, args.crop_top)
    if ok:
        print(f"{out}: {mmss(duration(out))}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
