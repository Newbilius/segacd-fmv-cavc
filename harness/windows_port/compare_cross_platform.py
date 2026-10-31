#!/usr/bin/env python3
"""Verify same-source Linux/Windows encoder and packed-stream equivalence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import analysis_logs


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


def read_normalized(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = {row["stream"]: row for row in csv.DictReader(stream, delimiter="\t")}
    if not rows:
        raise ValueError(f"normalized-source comparison is empty: {path}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate_data", type=Path)
    parser.add_argument("candidate_packed", type=Path)
    parser.add_argument("candidate_player", type=Path)
    parser.add_argument("candidate_source", type=Path)
    parser.add_argument("candidate_normalized_tsv", type=Path)
    parser.add_argument("--tsv", type=Path, required=True)
    parser.add_argument("--decisions-tsv", type=Path, required=True)
    args = parser.parse_args()

    baseline = args.baseline.resolve()
    identity = json.loads((baseline / "identity.json").read_text(encoding="utf-8"))
    source_hash = sha256(baseline / "source.mp4")
    if source_hash != identity["source_sha256"]:
        raise ValueError("baseline source container hash differs from its receipt")
    if sha256(args.candidate_source) != source_hash:
        raise ValueError("Windows encode did not use the exact Linux source bytes")
    if analysis_logs.av_versions()[0] != identity["encoder_version"]:
        raise ValueError("Linux and Windows public encoder versions differ")

    baseline_media = read_normalized(baseline / "normalized-source.tsv")
    candidate_media = read_normalized(args.candidate_normalized_tsv)
    if baseline_media.keys() != candidate_media.keys():
        raise ValueError("Linux and Windows normalized stream sets differ")
    rows = [["source container", source_hash, "IDENTICAL INPUT BYTES"]]
    for stream, left in baseline_media.items():
        right = candidate_media[stream]
        for key in ("normalization", "left_sha256", "right_sha256"):
            if left[key] != right[key]:
                raise ValueError(f"normalized {stream} {key} differs across platforms")
        for row, platform in ((left, "Linux"), (right, "Windows")):
            if (row["left_container_sha256"] != source_hash
                    or row["right_container_sha256"] != source_hash):
                raise ValueError(f"{platform} {stream} normalization used different source bytes")
        if stream == "video" and f"{identity['frames']} frames" not in left["normalization"]:
            raise ValueError("normalized video frame count differs from the baseline receipt")
        if left["left_sha256"] != left["right_sha256"]:
            raise ValueError(f"Linux {stream} self-comparison is not exact")
        if right["left_sha256"] != right["right_sha256"]:
            raise ValueError(f"Windows {stream} self-comparison is not exact")
        rows.append([f"decoded {stream}", left["left_sha256"], "IDENTICAL"])

    for name in PACKED_FILES:
        expected = sha256(baseline / "packed" / name)
        actual = sha256(args.candidate_packed / name)
        if actual != expected:
            raise ValueError(f"packed file differs: {name}: {expected} != {actual}")
        rows.append([name, actual, "IDENTICAL"])
    for name in PLAYER_FILES:
        expected = sha256(baseline / "player" / name)
        actual = sha256(args.candidate_player / name)
        if actual != expected:
            raise ValueError(f"assembled player binary differs: {name}: {expected} != {actual}")
        rows.append([name, actual, "IDENTICAL"])

    subprocess.run([
        sys.executable, str(ROOT / "harness/runtime_storage/compare.py"),
        str(baseline / "data"), str(args.candidate_data.resolve()),
        "--normalize-path-provenance", "--tsv", str(args.decisions_tsv),
    ], cwd=ROOT, check=True)
    rows.append(["saved decisions, numeric traces and audio PCM",
                 str(args.decisions_tsv), "IDENTICAL"])

    args.tsv.parent.mkdir(parents=True, exist_ok=True)
    with args.tsv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["artifact", "comparison", "result"])
        writer.writerows(rows)
    print(f"PASS: full same-source Linux/Windows comparison; {args.tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
