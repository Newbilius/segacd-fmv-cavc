#!/usr/bin/env python3
"""Encode independent parts of one profile and build a menu-free looping disc."""
from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import analysis_logs
import cavc_routing
import encode_config
import resource_tokens
import sim_artifact_cache
import source_inspection
import tmpfs_workspace
import process_tree
from source_range import frame_range

ROOT = Path(__file__).resolve().parents[1]
MAX_PARTS = encode_config.MAX_MULTIPART_PARTS
AUTO_PART_FRAMES = 65535
PACKED_FILES = ("HEADER.DAT", "BODY.DAT", "MOVIE.DAT", "paltab.bin", "palidx.bin")


def validate_windows_part_stem(stem):
    """Keep generated per-part profile/artifact names within Win32 limits."""
    if os.name == "nt" and len(stem) + len("-part064") > 240:
        raise ValueError(
            "multipart profile filename is too long for Windows; shorten its "
            "stem so generated part profiles stay within 240 characters")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def token(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def split_frames(frames, parts, offset=0):
    if type(parts) is not int or not 1 <= parts <= MAX_PARTS:
        raise ValueError(f"parts must be within 1..{MAX_PARTS}")
    if frames < parts * 2:
        raise ValueError("each part needs at least two output frames")
    count, extra = divmod(frames, parts)
    if count + bool(extra) > cavc_routing.MAX_FRAMES:
        raise ValueError(f"each part must fit {cavc_routing.MAX_FRAMES} frames; increase --parts")
    result = []
    start = offset
    for index in range(parts):
        end = start + count + (index < extra)
        result.append((start, end))
        start = end
    return result


def automatic_part_count(frames):
    """Minimum part count satisfying the automatic 65,535-frame policy."""
    if type(frames) is not int or frames < 2:
        raise ValueError("automatic splitting requires at least two output frames")
    parts = (frames + AUTO_PART_FRAMES - 1) // AUTO_PART_FRAMES
    if parts > MAX_PARTS:
        raise ValueError(
            f"{frames} output frames require {parts} parts; maximum is {MAX_PARTS}")
    return parts


def toml_text(data):
    lines = []
    def table(values, prefix=()):
        if prefix:
            lines.extend(("", "[" + ".".join(json.dumps(x) for x in prefix) + "]"))
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(json.dumps(key) + " = " + json.dumps(
                    value, ensure_ascii=False, allow_nan=False))
        for key, value in values.items():
            if isinstance(value, dict):
                table(value, (*prefix, key))
    table(data)
    return "\n".join(lines) + "\n"


@contextlib.contextmanager
def stem_lock(stems):
    if isinstance(stems, str):
        stems = (stems,)
    else:
        stems = tuple(stems)
    with resource_tokens.acquire_stems(stems):
        key = resource_tokens.HELD_STEMS_ENV
        previous = os.environ.get(key)
        os.environ.update(resource_tokens.held_stems_environment(stems))
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def run_logged(command, name, *, env=None, handoff=None):
    """Retain commands, PID, log, exit status and optional SIM lease handoff."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = ROOT / "logs" / f"multipart-{name}-{run_id}.log"
    receipt = {"command": [str(x) for x in command], "log": str(log),
               "started_at": run_id, "pid": None, "exit_code": None}
    environment = dict(os.environ if env is None else env)
    lease = None
    if handoff is not None:
        handoff.mkdir(parents=True)
        environment["SEGACD_TMPFS_HANDOFF"] = str(handoff)
    started = time.perf_counter()
    try:
        with log.open("w", encoding="utf-8") as output:
            output.write("$ " + " ".join(receipt["command"]) + "\n")
            output.flush()
            launch = {"cwd": ROOT, "env": environment, "stdout": output,
                      "stderr": subprocess.STDOUT}
            process = process_tree.popen(receipt["command"], **launch)
            receipt["pid"] = process.pid
            write_json(log.with_suffix(".json"), receipt)
            print(f"multipart: {name}: pid={process.pid} log={log}", flush=True)
            try:
                while process.poll() is None:
                    if handoff is not None and lease is None:
                        ready = handoff / "ready.json"
                        if ready.exists():
                            path = Path(json.loads(ready.read_text(encoding="utf-8"))["path"])
                            lease = tmpfs_workspace.lease_managed_path(path)
                            if lease is None:
                                raise RuntimeError(f"cannot pin completed SIM: {path}")
                            (handoff / "ack").write_text("pinned\n")
                    time.sleep(0.1)
            except BaseException:
                if process.poll() is None:
                    process_tree.terminate(process)
                process.wait()
                raise
            finally:
                receipt.update(
                    exit_code=process.poll(),
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    seconds=time.perf_counter()-started,
                )
                write_json(log.with_suffix(".json"), receipt)
        if process.returncode:
            raise RuntimeError(f"{name} failed ({process.returncode}); inspect {log}")
        print(f"multipart: {name}: complete in {receipt['seconds']:.1f}s", flush=True)
        return receipt, lease
    except BaseException:
        if lease is not None:
            lease.release()
        raise


def input_identity(profile, parts, *, environ=None):
    environment = dict(os.environ if environ is None else environ)
    encode_config.apply_profile_env(profile, environment)
    return {"schema": 1, "parts": parts, "encoder": analysis_logs.encoder_version(),
            "source": sim_artifact_cache.source_identity(profile.data["source"]["path"]),
            "settings": sim_artifact_cache.effective_environment(environment)}


def count_frames(profile, name):
    source = profile.section("source")
    with resource_tokens.acquire_tokens("cpu", count=1):
        geometry = source_inspection.probe(source["path"])
        video = profile.section("video")
        from video_geometry import source_filter, raw_filter, parse_ratio, endpoint_snap_filter
        sar = source.get("sar") or geometry["display_sar"] or "1:1"
        sar_num, sar_den = parse_ratio(sar)
        common = dict(src_sar_num=sar_num, src_sar_den=sar_den,
                      fit=video.get("fit", "pad"),
                      resize_filter=video.get("resize_filter", "lanczos"))
        master_vf = video.get("master_filter") or source_filter(
            int(video["width"]), int(video["height"]),
            geometry["display_width"], geometry["display_height"],
            denoise=bool(video.get("master_denoise", True)), **common)
        raw_vf = video.get("raw_filter") or raw_filter(
            int(video["width"]), int(video["height"]),
            geometry["display_width"], geometry["display_height"], **common)
        preprocess = source.get("preprocess", {})
        snap = preprocess.get("endpoint_snap", {})
        prefix = endpoint_snap_filter(snap.get("black_max", -1),
                                      snap.get("white_min", 256))
        if prefix:
            master_vf = f"{prefix},{master_vf}"
            raw_vf = f"{prefix},{raw_vf}"
        print(f"Counting exact output frames for {source['path']} (FFmpeg decode; may take the full source duration)")
        total = source_inspection.count_output_frames(
            source["path"], str(source["fps"]), duration=str(source["duration"]),
            video_filter=master_vf)
        raw_total = (source_inspection.count_output_frames(
            source["path"], str(source["fps"]), duration=str(source["duration"]),
            video_filter=raw_vf)
            if video.get("master_filter") or video.get("raw_filter") else total)
        if raw_total != total:
            raise ValueError(
                f"master and raw video filters produce different frame counts "
                f"({total} vs {raw_total}); align their timing")
        return total


def prepare(profile, parts, known_frames=None):
    identity = input_identity(profile, parts)
    stem = f"{profile.artifact_stem}-multipart{parts}-{token(identity)[:12]}"
    validate_windows_part_stem(stem)
    work = ROOT / "tmp" / stem
    plan_file = work / "plan.json"
    if plan_file.is_file():
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        if plan.get("identity") != identity:
            raise ValueError("multipart plan identity differs")
    else:
        total = known_frames if known_frames is not None else count_frames(profile, stem)
        bounds = frame_range(profile.section("source")) or (0, total)
        if bounds[1] > total:
            raise ValueError("profile source range exceeds decoded source frames")
        plan = {"identity": identity, "stem": stem, "profile": str(profile.path),
                "source_frames": total, "ranges": split_frames(bounds[1]-bounds[0], parts, bounds[0])}
        write_json(plan_file, plan)
    bounds = frame_range(profile.section("source")) or (0, plan["source_frames"])
    expected = [list(pair) for pair in split_frames(bounds[1]-bounds[0], parts, bounds[0])]
    if [list(pair) for pair in plan["ranges"]] != expected or plan["stem"] != stem:
        raise ValueError("multipart plan ranges or output stem differ")
    directory = work / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    profiles = []
    for number, (start, end) in enumerate(plan["ranges"], 1):
        data = copy.deepcopy(profile.data)
        # The count is already represented by the plan and frame ranges. Keep
        # per-part profiles identical whether the count came from TOML or CLI.
        data.pop("multipart", None)
        data["source"].update(start_frame=start, end_frame=end)
        data["output"].update(emit_decisions=True, reuse=False)
        path = directory / f"{stem}-part{number:03}.toml"
        text = toml_text({"schema_version": encode_config.SCHEMA_VERSION, **data})
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        profiles.append(encode_config.load_profile(path))
    return plan, profiles


def part_identity(plan, number, profile):
    return {"multipart": token(plan["identity"]), "part": number,
            "range": list(frame_range(profile.section("source")))}


def completed_part(plan, number, profile):
    marker = ROOT / profile.artifact_dir / "multipart-complete.json"
    if not marker.exists():
        return None
    record = json.loads(marker.read_text(encoding="utf-8"))
    if record.get("identity") != part_identity(plan, number, profile):
        raise ValueError(f"part {number}: completion identity differs")
    for name in PACKED_FILES:
        path = marker.parent / name
        if not path.is_file() or digest(path) != record["files"][name]:
            raise ValueError(f"part {number}: completed file changed or missing: {path}")
    return record


def encode_part(plan, number, profile):
    with stem_lock(profile.lock_stems):
        handoff = ROOT / "tmp" / plan["stem"] / ("handoff-" + uuid.uuid4().hex)
        sim, lease = run_logged([sys.executable, ROOT / "tools/sim.py", profile.path],
                                f"{plan['stem']}-part{number:03}-encode", handoff=handoff)
        if lease is None:
            raise RuntimeError("SIM exited without its completion lease")
        try:
            import build_disc
            build_disc.build(profile.path, pack_only=True)
            pack = {"stage": "pack", "profile": str(profile.path), "exit_code": 0}
            import player_constants
            folder = ROOT / profile.artifact_dir
            with (folder / "HEADER.DAT").open("rb") as stream:
                constants = player_constants.parse_header_sector(stream.read(2048))
            start, end = frame_range(profile.section("source"))
            if constants.frames != end-start:
                raise ValueError(f"part {number}: packed frame count differs")
            record = {"identity": part_identity(plan, number, profile), "frames": constants.frames,
                      "profile": str(profile.path), "sim": sim, "pack": pack,
                      "files": {name: digest(folder/name) for name in PACKED_FILES}}
            write_json(folder / "multipart-complete.json", record)
            return record
        finally:
            lease.release()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Set [multipart] parts = N in the profile or pass --parts N. "
               "Completed parts are reused; the ISO is built when every part is complete. "
               "See README.md: Multipart encoding (CLI).")
    parser.add_argument("profile", type=Path)
    parser.add_argument("--parts", type=int, metavar="N",
                        help=f"Number of parts (1..{MAX_PARTS}); overrides [multipart].parts")
    parser.add_argument("--part", type=int, metavar="K",
                        help="Encode only part K (1-based); omission completes all parts")
    parser.add_argument("--build-only", action="store_true", help="Build from already completed parts")
    parser.add_argument("--encode-only", action="store_true", help="Encode/reuse parts without building a disc")
    parser.add_argument("--known-frames", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="Include the playback DEBUG HUD")
    parser.add_argument("--security-region", choices=("jp", "us"), default="jp")
    parser.add_argument("--disc-label", help="ISO volume label (1..32 ASCII letters, digits, or underscores)")
    args = parser.parse_args(argv)
    if args.disc_label is not None:
        try:
            args.disc_label = encode_config.normalize_disc_label(args.disc_label)
        except ValueError as exc:
            parser.error(str(exc))
    if args.parts is not None and not 1 <= args.parts <= MAX_PARTS:
        parser.error(f"--parts must be within 1..{MAX_PARTS}")
    if args.known_frames is not None and args.known_frames < 2:
        parser.error("--known-frames must be at least 2")
    if args.part is not None and args.part < 1:
        parser.error("--part must be within 1..--parts (1-based)")
    if args.part is not None and args.build_only:
        parser.error("--part cannot be combined with --build-only")
    try:
        profile = encode_config.load_profile(args.profile)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid encode profile: {exc}")
    if args.parts is None:
        args.parts = profile.section("multipart").get("parts")
    if args.parts is None:
        parser.error("set --parts N or [multipart].parts in the profile")
    if args.part is not None and args.part > args.parts:
        parser.error(f"--part must be within 1..{args.parts} (1-based)")
    return args, profile


def main():
    args, profile = parse_args()
    os.chdir(ROOT)
    (ROOT / "logs").mkdir(exist_ok=True)
    with stem_lock(f"multipart-{profile.artifact_stem}-{args.parts}"):
        plan, profiles = prepare(profile, args.parts, known_frames=args.known_frames)
        selected = {args.part} if args.part is not None else set(range(1, args.parts+1))
        records = []
        for number, part in enumerate(profiles, 1):
            record = completed_part(plan, number, part)
            if record is not None:
                print(f"multipart: part {number}/{args.parts}: verified completed part reused", flush=True)
            elif number in selected and not args.build_only:
                print(f"multipart: part {number}/{args.parts}: frames {plan['ranges'][number-1]}", flush=True)
                record = encode_part(plan, number, part)
            records.append(record)
        missing = [i+1 for i, record in enumerate(records) if record is None]
        if missing:
            print(f"multipart: ISO deferred; unfinished parts: {', '.join(map(str, missing))}", flush=True)
            if args.build_only:
                return 2
            return 0
        if args.encode_only:
            print("multipart: all parts complete; encode-only requested, disc build skipped", flush=True)
            return 0
        import multipart_build
        multipart_build.build(plan, profiles, records, debug=args.debug,
                              region=args.security_region,
                              disc_label=(args.disc_label if args.disc_label is not None
                                  else profile.data.get("release", {}).get("disc_label")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
