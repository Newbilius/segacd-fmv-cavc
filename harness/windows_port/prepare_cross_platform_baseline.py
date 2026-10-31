#!/usr/bin/env python3
"""Collect comparable Linux full-source artifacts for the Windows CI job."""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import encode_config
import analysis_logs


DATA_FILES = (
    "decisions.pkl",
    "stats.npz",
    "buffer_remaining.npz",
    "seg_palettes.npz",
    "miss_masks.npy",
    "palettes.bin",
    "audio_22k05_s16_mono.wav",
    "audio_playback_adpcm22_rf5c.wav",
)
PACKED_FILES = (
    "HEADER.DAT", "BODY.DAT", "MOVIE.DAT", "paltab.bin", "palidx.bin",
)
PLAYER_FILES = (
    "movieplay_ip.bin", "movieplay_sp.bin", "movieplay_sp_ext.bin",
    "movieplay_boot.bin",
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    profile = encode_config.load_profile(args.profile)
    source = Path(profile.data["source"]["path"]).resolve()
    data = profile.output_dir
    packed = profile.artifact_dir
    destination = args.destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "data").mkdir()
    (destination / "packed").mkdir()
    (destination / "player").mkdir()

    if not source.is_file():
        raise FileNotFoundError(f"missing normalized Linux source: {source}")
    shutil.copyfile(source, destination / "source.mp4")
    (destination / "source.sha256").write_text(
        sha256(source) + "\n", encoding="ascii", newline="\n")

    for name in DATA_FILES:
        path = data / name
        if not path.is_file():
            raise FileNotFoundError(f"missing completed Linux sim output: {path}")
        shutil.copyfile(path, destination / "data" / name)
    for name in PACKED_FILES:
        path = packed / name
        if not path.is_file():
            raise FileNotFoundError(f"missing verified Linux packed output: {path}")
        shutil.copyfile(path, destination / "packed" / name)
    for name in PLAYER_FILES:
        path = profile.build_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"missing assembled Linux player binary: {path}")
        shutil.copyfile(path, destination / "player" / name)

    normalized_tsv = destination / "normalized-source.tsv"
    subprocess.run([
        sys.executable,
        str(ROOT / "harness/windows_port/compare_normalized_source.py"),
        str(source), str(source), "--tsv", str(normalized_tsv),
    ], cwd=ROOT, check=True)
    duration = Fraction(str(profile.data["source"]["duration"]))
    fps = Fraction(str(profile.data["source"]["fps"]))
    frame_count = round(float(duration * fps))
    if abs(float(duration * fps) - frame_count) > 1e-6:
        raise ValueError("profile duration/fps do not describe a whole frame count")
    identity = {
        "source_sha256": sha256(source),
        "encoder_version": analysis_logs.av_versions()[0],
        "frames": frame_count,
        "profile": str(profile.path),
    }
    (destination / "identity.json").write_text(
        json.dumps(identity, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Linux cross-platform baseline prepared: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
