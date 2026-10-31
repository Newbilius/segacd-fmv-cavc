"""FFprobe inspection and exact output-frame counting for CLI planning."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from fractions import Fraction
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def probe(path: str | Path, ffprobe: str = "ffprobe") -> dict:
    source = Path(path).expanduser().resolve()
    executable = shutil.which(ffprobe) or ffprobe
    command = [executable, "-v", "error", "-show_streams", "-show_format",
               "-of", "json", str(source)]
    result = subprocess.run(command, check=False, capture_output=True,
                           encoding="utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"FFprobe failed for {source}: {result.stderr.strip()}")
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"FFprobe returned invalid JSON for {source}: {exc}") from exc
    videos = [stream for stream in metadata.get("streams", [])
              if stream.get("codec_type") == "video"]
    if not videos:
        raise ValueError(f"no video stream in {source}")
    video = videos[0]
    rotation = 0.0
    for side_data in video.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = float(side_data["rotation"])
            break
    if not rotation:
        try:
            rotation = float(video.get("tags", {}).get("rotate", 0))
        except (TypeError, ValueError):
            rotation = 0.0
    quarter_turn = round(rotation / 90) % 2
    display_width = int(video["height"] if quarter_turn else video["width"])
    display_height = int(video["width"] if quarter_turn else video["height"])
    sar = video.get("sample_aspect_ratio", "1:1")
    if quarter_turn and sar not in (None, "N/A", "0:1"):
        numerator, denominator = sar.split(":", 1)
        sar = f"{denominator}:{numerator}"
    return {"path": str(source), "metadata": metadata, "video": video,
            "rotation": rotation, "display_width": display_width,
            "display_height": display_height, "display_sar": sar,
            "audio": [s for s in metadata.get("streams", [])
                      if s.get("codec_type") == "audio"]}


def source_duration(info: dict) -> str | None:
    """Return a valid probed duration, including exact stream time-base fallback."""
    video = info.get("video", {})
    format_info = info.get("metadata", {}).get("format", {})
    for value in (format_info.get("duration"), video.get("duration")):
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return str(value)
    try:
        ticks = int(video["duration_ts"])
        duration = Fraction(ticks) * Fraction(video["time_base"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if duration <= 0:
        return None
    return str(float(duration))


def aligned_audio_filter(audio_filter: str, duration: str) -> str:
    """Preserve source timestamps, then pad/trim audio to the video duration."""
    chain = ["aresample=async=1:first_pts=0"]
    if audio_filter:
        chain.insert(0, audio_filter)
    chain.extend((f"apad=whole_dur={duration}", f"atrim=duration={duration}"))
    return ",".join(chain)


def count_output_frames(path: str | Path, fps: str, *, duration: str | None = None,
                        start_frame: int | None = None,
                        end_frame: int | None = None,
                        video_filter: str = "null",
                        log_dir: str | Path | None = None,
                        ffmpeg: str = "ffmpeg") -> int:
    """Count frames on the same fps/range clock used by source_range.video_filter."""
    source = Path(path).expanduser().resolve()
    executable = shutil.which(ffmpeg) or ffmpeg
    command = [executable, "-hide_banner", "-loglevel", "error", "-threads", "1",
               "-filter_threads", "1"]
    if duration is not None:
        command += ["-t", str(duration)]
    command += ["-i", str(source), "-map", "0:v:0"]
    bounds = None if start_frame is None and end_frame is None else (start_frame, end_frame)
    from source_range import video_filter as timed_video_filter
    command += ["-vf", timed_video_filter(video_filter, fps, bounds), "-an", "-progress", "pipe:1",
                "-nostats", "-f", "null", "-"]
    logs = ROOT / "logs" if log_dir is None else Path(log_dir)
    if not logs.is_absolute():
        logs = ROOT / logs
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = logs / f"source-count-{stamp}.log"
    receipt_path = log.with_suffix(".json")
    receipt = {"command": command, "source": str(source), "log": str(log),
               "started_at": stamp, "pid": None, "exit_code": None}
    counts = []
    started = time.perf_counter()
    with log.open("w", encoding="utf-8", newline="\n") as output:
        output.write("$ " + subprocess.list2cmdline(command) + "\n")
        output.flush()
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", bufsize=1)
        receipt["pid"] = process.pid
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(f"FFmpeg exact count: pid={process.pid}; log={log}", flush=True)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output.write(line)
                output.flush()
                if line.startswith("frame=") and line.partition("=")[2].strip().isdigit():
                    counts.append(int(line.partition("=")[2].strip()))
                    print(f"FFmpeg count progress: frame={counts[-1]}", flush=True)
            process.stdout.close()
            code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            receipt.update(exit_code=process.returncode, interrupted=True)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            raise
    receipt.update(exit_code=code, ended_at=datetime.now(timezone.utc).isoformat(),
                   seconds=time.perf_counter() - started)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if code:
        raise ValueError(f"FFmpeg frame count failed for {source}; inspect {log}")
    counts = counts or [int(line.partition("=")[2]) for line in log.read_text(encoding="utf-8").splitlines()
              if line.startswith("frame=") and line.partition("=")[2].isdigit()]
    if not counts or counts[-1] < 2:
        raise ValueError(f"source has fewer than two output frames: {source}")
    return counts[-1]
