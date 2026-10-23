"""Film a test run (cuo-gka): record the client windows on the given agent ports while a command runs,
then save one clip per window in ~/Movies/ClassicUO-agent-clips/ and print the row for the vault's
Footage log (AGENTS.md, "Footage as we test").

    python3 tools/video/film.py --ports 5661-5665 --what "a party on the lich scene" --bead cuo-4ar \\
        -- uv run --project brain uo-brain fleet brain/fleets/party-lich.toml --windowed

The command runs as given (from the current folder). Recording starts once every port has a client
with a window (a fleet starts and logs them in one by one; up to --wait seconds), and stops when the
command ends (or on Ctrl-C). Each window becomes "YYYY-MM-DD <what> (<bead>) - <character>.mp4", the
macOS title bar cropped off; with several, also a "... - all.mp4" of them side by side. The raw .mov
files are kept in the clips folder's raw/ (AGENTS.md: Harry clears it, nobody else). A headless client has no window: film a
fleet with --windowed. Recording loads the machine: say so in the experiment's row.

Needs the recorder built (tools/video/build.sh; done here if missing), ffmpeg, and the Screen Recording
permission for the terminal it runs from.

ScreenCaptureKit gets no frames while the Mac's screen is locked (it sees no display), which is how
unattended runs go. Then (--source auto, the default) each client films itself instead (cuo-cym.32): its
"record" RPC reads its own frames, --client-fps a second, into a FIFO that ffmpeg reads (raw/PORT.mp4).
That costs the game thread a few milliseconds a frame read; --source screen or client forces either.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINCAPS = HERE / "bin" / "wincaps"
CLIPS = Path.home() / "Movies" / "ClassicUO-agent-clips"
TITLE_BAR = 28  # points: the macOS title bar over a client window, cropped off (times the window's scale)


def ports_of(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def listener(port: int) -> int | None:
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.split()]
    return pids[0] if pids else None


def status(port: int) -> dict:
    """The client's status over its agent RPC (one JSON line each way); {} when it doesn't answer."""
    return rpc(port, "status")


def rpc(port: int, method: str, params: dict | None = None) -> dict:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
            s.sendall((json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n").encode())
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return {}
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    msg = json.loads(line.decode()) if line.strip() else {}
                    if msg.get("id") == 1:
                        return msg.get("result") or {}
    except (OSError, ValueError):
        return {}


def wait_for_windows(ports: list[int], wait_s: float) -> dict[int, int]:
    """port -> client pid, once each port's client is in game with its window (or wait_s is up)."""
    began, found = time.monotonic(), {}
    while time.monotonic() - began < wait_s:
        for port in ports:
            if port in found:
                continue
            pid = listener(port)
            st = status(port) if pid else {}
            if pid and st.get("in_game") and not st.get("headless"):
                found[port] = pid
        if len(found) == len(ports):
            break
        time.sleep(2)
    return found


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def recorded(log: Path) -> dict[str, tuple[str, float]]:
    """Each recording's window title and pixels a point, from the recorder's log ("window 'Roland - ClassicUO
    - 1.1.0.0' (x, y, w, h) -> WxH"): the scale is 1 on an ordinary display, 2 on a Retina one."""
    out = {}
    for line in log.read_text().splitlines() if log.exists() else []:
        m = re.match(r"wincaps (.+\.mov): window '(.*)' \(([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+)\) -> (\d+)x(\d+)", line)
        if m and float(m.group(5)):
            out[Path(m.group(1)).name] = (m.group(2), int(m.group(7)) / float(m.group(5)))
    return out


def screen_locked() -> bool:
    """Whether ScreenCaptureKit sees no display (the screen locked or asleep): then it records nothing."""
    if not WINCAPS.exists():
        subprocess.run([str(HERE / "build.sh")], check=True)
    r = subprocess.run([str(WINCAPS), "--displays"], capture_output=True, text=True)
    return r.stdout.strip() == "0"


class ClientRecording:
    """One client filming itself (cuo-cym.32): its frames go into a FIFO, ffmpeg makes raw/PORT.mp4 of them."""

    def __init__(self, port: int, raw_dir: Path, fps: int, log):
        self.port, self.log = port, log
        self.fifo = raw_dir / f"{port}.rgba"
        self.out = raw_dir / f"{port}.mp4"
        os.mkfifo(self.fifo)
        self.started = rpc(port, "record", {"path": str(self.fifo), "fps": fps})
        self.ffmpeg = None
        if not self.started.get("recording"):
            print(f"film: port {port} won't record: {self.started}", file=log, flush=True)
            return
        w, h = self.started["width"], self.started["height"]
        Path(f"{self.out}.start").write_text(f"{self.started['start']:.3f}\n")
        self.ffmpeg = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
             "-framerate", str(fps), "-i", str(self.fifo), "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", str(self.out)], stderr=log)

    def check(self) -> dict:
        st = rpc(self.port, "record")
        if st:
            print(f"client {self.out.name}: {json.dumps(st)}", file=self.log, flush=True)
        return st

    def stop(self) -> None:
        if self.ffmpeg is None:
            return
        self.check()
        rpc(self.port, "record", {"stop": True})  # a client still running: the clip ends now; one gone ended it
        try:
            self.ffmpeg.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.ffmpeg.terminate()
        self.fifo.unlink(missing_ok=True)


def scales(log: Path) -> dict[str, float]:
    return {name: scale for name, (_, scale) in recorded(log).items()}


def make_clips(raw_dir: Path, stem: str, out_dir: Path, default_scale: float = 2) -> list[Path]:
    """One clip per raw recording, named after its window's character, the title bar cropped; with several,
    all of them side by side too."""
    made = []
    seen = recorded(raw_dir / "wincaps.log")
    names = filmed_by_clients(raw_dir / "recorder.log")
    for raw in sorted(raw_dir.glob("*.mov")) + sorted(raw_dir.glob("*.mp4")):
        if raw.suffix == ".mp4":  # a client's own frames: no title bar
            who, crop = names.get(raw.name, raw.stem), 0
        else:
            title, scale = seen.get(raw.name, (raw.stem, default_scale))
            who, crop = title.split(" - ClassicUO")[0] or raw.stem, round(TITLE_BAR * scale)
        out = out_dir / f"{stem} - {who}.mp4"
        if to_mp4(raw, out, crop):
            made.append(out)
    if len(made) > 1:
        grid = out_dir / f"{stem} - all.mp4"
        if side_by_side(made, grid):
            made.append(grid)
    return made


def filmed_by_clients(log: Path) -> dict[str, str]:
    """Each client recording's character, from the recorder's log ("client 5661.mp4 is Roland")."""
    out = {}
    for line in log.read_text().splitlines() if log.exists() else []:
        m = re.match(r"client (\S+\.mp4) is (.+)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def to_mp4(raw: Path, out: Path, crop_top: int) -> bool:
    crop = f"crop=iw:ih-{crop_top}:0:{crop_top}," if crop_top else ""
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-vf", f"{crop}fps=30", "-an",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(out)])
    return r.returncode == 0


def side_by_side(clips: list[Path], out: Path, width: int = 1920) -> bool:
    """The windows in a grid (2 across, more for 5 or more), each scaled to fit, starting together."""
    n = len(clips)
    cols = 2 if n <= 4 else 3
    rows = -(-n // cols)
    w = width // cols
    h = w * 9 // 16 // 2 * 2
    inputs = sum((["-i", str(c)] for c in clips), [])
    scaled = "".join(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                     f"setpts=PTS-STARTPTS[v{i}];" for i in range(n))
    layout = "|".join(f"{(i % cols) * w}_{(i // cols) * h}" for i in range(n))
    fc = scaled + "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=black:shortest=0[v]"
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-an",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-s", f"{cols * w}x{rows * h}", str(out)])
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ports", help="agent ports of the clients to film: 5661-5665 or 5577,5580")
    ap.add_argument("--what", required=True, help="what the run shows, for the file names and the log row")
    ap.add_argument("--bead", default="", help="the bead the run is for")
    ap.add_argument("--wait", type=float, default=180, help="seconds to wait for the clients' windows (default 180)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--source", choices=["auto", "screen", "client"], default="auto",
                    help="screen (ScreenCaptureKit), client (each client's own frames, works with the screen locked) "
                         "or auto: client when the screen is locked (default)")
    ap.add_argument("--client-fps", type=int, default=15, help="frames a second a client reads of itself (default 15)")
    ap.add_argument("--scale", type=float, default=2, help="pixels a point, where the recorder's log doesn't say")
    ap.add_argument("--out", type=Path, default=CLIPS, help=f"where the clips go (default {CLIPS})")
    ap.add_argument("--remake", type=Path, metavar="RAW_DIR",
                    help="no command: make the clips again from a folder of raw recordings (a crop gone wrong)")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command to run and film")
    args = ap.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.remake:
        day = args.remake.name[:10]
        made = make_clips(args.remake, f"{day} {args.what}" + (f" ({args.bead})" if args.bead else ""), args.out,
                          args.scale)
        for m in made:
            print(f"  {m.name}  {mmss(duration(m))}")
        return 0 if made else 1
    if not command or not args.ports:
        ap.error("give --ports and, after --, the command to film")
    if not WINCAPS.exists():
        subprocess.run([str(HERE / "build.sh")], check=True)
    ports = ports_of(args.ports)
    day = datetime.date.today().isoformat()
    stem = f"{day} {args.what}" + (f" ({args.bead})" if args.bead else "")
    # Raw recordings are kept (Harry, 2026-10-10), named as AGENTS.md says: raw/YYYY-MM-DD <what> (<id>)/.
    raw_dir = args.out / "raw" / f"{stem.removesuffix(f' ({args.bead})')} ({(args.bead + ' ') if args.bead else ''}" \
                                 f"{time.strftime('%H%M%S')})"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stop_file = raw_dir / "stop"

    began = time.time()
    run = subprocess.Popen(command)
    windows = wait_for_windows(ports, args.wait)
    missing = [p for p in ports if p not in windows]
    if missing:
        print(f"film: no window on {missing} after {args.wait:.0f} s (headless, or not in game); filming the rest",
              file=sys.stderr)
    names = {port: (status(port).get("player") or f"port {port}") for port in windows}
    recorder = None
    source = args.source
    if source == "auto":
        source = "client" if screen_locked() else "screen"
    if windows and source == "client":
        log = (raw_dir / "recorder.log").open("a")
        clients = []
        for port in windows:
            print(f"client {port}.mp4 is {names[port]}", file=log, flush=True)
            clients.append(ClientRecording(port, raw_dir, args.client_fps, log))
        started = time.time()
        print(f"film: {', '.join(names.values())} filming themselves from {time.strftime('%H:%M:%S')} "
              f"(the screen is locked or --source client)", file=sys.stderr)
        try:
            checked = started
            while run.poll() is None:
                time.sleep(1)
                if time.time() - checked >= 30:
                    checked = time.time()
                    for c in clients:
                        c.check()
        except KeyboardInterrupt:
            run.terminate()
        code = run.wait()
        for c in clients:
            c.stop()
        log.close()
        return finish(raw_dir, stem, args, command, started - began, code)
    if windows:
        cmd = [str(WINCAPS), "--stop", str(stop_file), "--fps", str(args.fps)]
        for port, pid in windows.items():
            cmd += [str(pid), str(raw_dir / f"{port}.mov")]
        recorder = subprocess.Popen(cmd, stderr=(raw_dir / "wincaps.log").open("w"))
        started = time.time()
        print(f"film: recording {', '.join(names.values())} from {time.strftime('%H:%M:%S')}", file=sys.stderr)
    try:
        code = run.wait()
    except KeyboardInterrupt:
        run.terminate()
        code = run.wait()
    if recorder is None:
        return code
    stop_file.touch()
    try:
        recorder.wait(timeout=60)
    except subprocess.TimeoutExpired:
        recorder.terminate()

    return finish(raw_dir, stem, args, command, started - began, code)


def finish(raw_dir: Path, stem: str, args, command: list[str], offset: float, code: int) -> int:
    day = datetime.date.today().isoformat()
    made = make_clips(raw_dir, stem, args.out, args.scale)
    print(f"\nfilm: {len(made)} clips in {args.out} (recording began {offset:.0f} s into the command):")
    for m in made:
        print(f"  {m.name}  {mmss(duration(m))}")
    if made:
        files = ", ".join(f"`{m.name}` ({mmss(duration(m))})" for m in made)
        print("\nRow for the vault's Footage log (fill in what it shows and when, anything staged, where used):")
        print(f"| {day} | {files} | {args.what} | `{shlex.join(command)}` | {args.bead} | | |")
        raws = list(raw_dir.glob("*.mov")) + list(raw_dir.glob("*.mp4"))
        size = sum(r.stat().st_size for r in raws) / 1e9
        print("\nRow for its Raw recordings table:")
        print(f"| `{raw_dir.name}` | the window recordings behind those clips, one per client | {len(raws)}, {size:.2f} GB |")
    return code


if __name__ == "__main__":
    sys.exit(main())
