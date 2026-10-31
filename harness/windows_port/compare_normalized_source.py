#!/usr/bin/env python3
"""Compare decoded video/audio content across differently encoded containers."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


VIDEO_DECODE = [
    "-map", "0:v:0", "-c:v", "rawvideo", "-pix_fmt", "yuv420p",
    "-f", "hash", "-hash", "sha256", "-",
]
AUDIO_DECODE = [
    "-map", "0:a:0", "-c:a", "pcm_s16le", "-ar", "22050", "-ac", "1",
    "-f", "hash", "-hash", "sha256", "-",
]


def probe(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-count_frames", "-show_streams",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    streams = json.loads(result.stdout)["streams"]
    video = next((stream for stream in streams
                  if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams
                  if stream.get("codec_type") == "audio"), None)
    if video is None:
        raise ValueError(f"{path}: no video stream")
    return {"video": video, "audio": audio}


def raw_hash(ffmpeg: str, path: Path, options: list[str]) -> str:
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), *options],
        check=True, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    value = result.stdout.strip()
    if not value.startswith("SHA256="):
        raise ValueError(f"{path}: unexpected FFmpeg hash output {value!r}")
    return value.partition("=")[2].lower()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--tsv", type=Path, required=True)
    args = parser.parse_args()

    ffmpeg = shutil.which(str(args.ffmpeg)) or str(args.ffmpeg)
    ffprobe = shutil.which(str(args.ffprobe)) or str(args.ffprobe)
    sources = (args.left.resolve(), args.right.resolve())
    container_hashes = [file_hash(path) for path in sources]
    metadata = [probe(ffprobe, path) for path in sources]
    left_video, right_video = metadata[0]["video"], metadata[1]["video"]
    video_fields = ("width", "height", "avg_frame_rate", "nb_read_frames")
    for field in video_fields:
        if left_video.get(field) != right_video.get(field):
            raise ValueError(
                f"video {field} differs: {sources[0]}={left_video.get(field)!r}, "
                f"{sources[1]}={right_video.get(field)!r}")
    if (metadata[0]["audio"] is None) != (metadata[1]["audio"] is None):
        raise ValueError("only one source has an audio stream")

    rows = []
    specs = [("video", VIDEO_DECODE,
              f"{left_video['width']}x{left_video['height']} YUV420P; "
              f"{left_video.get('avg_frame_rate')} fps; "
              f"{left_video.get('nb_read_frames')} frames")]
    if metadata[0]["audio"] is not None:
        specs.append(("audio", AUDIO_DECODE, "mono PCM S16LE, 22050 Hz"))
    for stream, options, description in specs:
        left_hash = raw_hash(ffmpeg, sources[0], options)
        right_hash = raw_hash(ffmpeg, sources[1], options)
        result = "IDENTICAL" if left_hash == right_hash else "DIFFERENT"
        rows.append([
            stream, description, str(sources[0]), container_hashes[0],
            str(sources[1]), container_hashes[1], left_hash, right_hash,
            result,
        ])
    args.tsv.parent.mkdir(parents=True, exist_ok=True)
    with args.tsv.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "stream", "normalization", "left_source",
            "left_container_sha256", "right_source",
            "right_container_sha256", "left_sha256", "right_sha256",
            "result",
        ])
        writer.writerows(rows)
    for row in rows:
        print(f"{row[0]}: {row[-1]} ({row[6]})")
    if any(row[-1] != "IDENTICAL" for row in rows):
        return 1
    print(f"normalized streams match; {args.tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
