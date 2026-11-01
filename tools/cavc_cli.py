"""Native host CLI for CAVC encode and disc-image production."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import textwrap
import traceback
import copy
import hashlib
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_disc
import encode_config
import gpu_quant
import process_tree
import resource_tokens

# Windows may start Python with a legacy console encoding such as cp1251.
# Child tools print Japanese diagnostics, so keep their captured stream UTF-8.
if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Child stages write reports with non-ASCII profile/source text. Preserve
    # UTF-8 file defaults even when cavc_cli.py is invoked outside python.ps1.
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")


def run_stage(name: str, command: list[str], *, env: dict[str, str]) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"windows-cli-{name}-{stamp}.log"
    receipt_path = log.with_suffix(".json")
    receipt = {"command": command, "log": str(log), "started_at": stamp,
               "pid": None, "exit_code": None,
               "device": env.get("CBRSIM_DEVICE", "auto")}
    start = time.perf_counter()
    with log.open("w", encoding="utf-8", newline="\n") as output:
        output.write("$ " + subprocess.list2cmdline(command) + "\n")
        output.flush()
        launch = {
            "cwd": ROOT,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        process = process_tree.popen(command, **launch)
        receipt["pid"] = process.pid
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output.write(line)
                output.flush()
                print(line, end="", flush=True)
            process.stdout.close()
            code = process.wait()
        except KeyboardInterrupt:
            process_tree.terminate(process)
            code = process.wait()
            receipt.update(
                exit_code=code,
                interrupted=True,
                ended_at=datetime.now(timezone.utc).isoformat(),
                seconds=time.perf_counter()-start,
            )
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            raise
    receipt.update(
        exit_code=code,
        ended_at=datetime.now(timezone.utc).isoformat(),
        seconds=time.perf_counter()-start,
    )
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if code:
        raise RuntimeError(f"{name} failed with exit code {code}; inspect {log}")
    return receipt


def apply_set_values(data: dict, assignments: dict[str, object]) -> dict:
    """Apply already parsed --set values before validation or frame planning."""
    resolved = copy.deepcopy(data)
    for key_path, value in assignments.items():
        cursor = resolved
        parts = key_path.split(".")
        for part in parts[:-1]:
            if part not in cursor:
                cursor[part] = {}
            if not isinstance(cursor[part], dict):
                raise ValueError(f"unknown configuration path: {key_path}")
            cursor = cursor[part]
        cursor[parts[-1]] = value
    return resolved


def claim_output_identity(path: Path, receipt: dict, output_paths: list[Path]) -> None:
    """Check and reserve a named output while its artifact lock is held."""
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("config_digest") != receipt["config_digest"]:
            raise ValueError(
                f"output name {receipt['name']!r} already belongs to another "
                "source/config; choose a different --name")
        return
    occupied = [item for item in output_paths if item.exists()]
    if occupied:
        raise ValueError(
            f"output name {receipt['name']!r} already has files without a "
            f"matching identity receipt: {occupied[0]}; choose another --name")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(receipt, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())


def safe_artifact_name(name: str | None, source: Path) -> str:
    """Return a Windows-safe ASCII stem for direct-source output artifacts."""
    default_stem = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "-"
        for char in source.stem).strip(".-_") or "movie"
    stem = default_stem if name is None else name
    import re
    reserved_component = stem.split(".", 1)[0]
    if (len(stem) > 180 or stem.endswith(".")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", stem)
            or re.fullmatch(
                r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])",
                reserved_component, re.I)):
        raise ValueError("--name must be a safe ASCII filename stem (not reserved; max 180 characters)")
    return stem


def source_byte_identity(source: Path) -> dict:
    """Hash input bytes for stable output naming and collision protection."""
    import sim_artifact_cache
    print(f"Hashing source bytes for output identity: {source}", flush=True)
    identity = sim_artifact_cache.source_identity(source)
    print(f"Source identity: {identity['bytes']} bytes, sha256={identity['sha256']}",
          flush=True)
    return identity


def named_output_paths(stem: str) -> list[Path]:
    """List existing artifacts whose generated filename belongs to a name."""
    output_root = ROOT / "out"
    paths = [ROOT / "tmp" / stem]
    if not output_root.is_dir():
        return paths
    folded = stem.casefold()
    for candidate in output_root.iterdir():
        candidate_name = candidate.name.casefold()
        if candidate_name == folded or (
                candidate_name.startswith(folded)
                and candidate_name[len(folded)] in "._-"):
            paths.append(candidate)
    return paths


def write_profile_atomic(path: Path, serialized: str) -> None:
    """Publish a complete frozen TOML without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=path.name + ".", suffix=".tmp", delete=False) as output:
            temporary_path = Path(output.name)
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def print_resolved_plan(profile, *, action: str, fps_origin: str,
                        duration_origin: str,
                        source_info: dict | None = None,
                        total_frames: int | None = None,
                        output_frames: int | None = None,
                        part_count: int | None = None,
                        source_bounds: tuple[int, int] | None = None,
                        iso_path: Path | None = None,
                        disc_label: str | None = None) -> None:
    source = profile.data["source"]
    video = profile.data["video"]
    print("Resolved conversion plan:")
    print(f"  Input: {source['path']}")
    if source_info is not None:
        video_stream = source_info["video"]
        timing = ", ".join(
            f"{key}={video_stream[key]}" for key in ("avg_frame_rate", "r_frame_rate")
            if video_stream.get(key) not in (None, "", "N/A")) or "unavailable"
        audio_indexes = [stream.get("index") for stream in source_info["audio"]]
        audio_selection = (f"available indexes={audio_indexes}; selected=0:a:0"
                           if audio_indexes else
                           "available indexes=none; aligned silent audio")
        print(f"  Selected video stream: 0:v:0 (index {video_stream.get('index', 'unknown')}); "
              f"coded={video_stream.get('width')}x{video_stream.get('height')}; "
              f"display={source_info['display_width']}x{source_info['display_height']}; "
              f"SAR={source_info['display_sar']}; rotation={source_info['rotation']} degrees")
        print(f"  Source video timing: {timing}; audio {audio_selection}")
    print(f"  Duration: {source['duration']} s ({duration_origin})")
    print(f"  FPS: {source['fps']} ({fps_origin})")
    print(f"  Cold cap: {profile.data['encoder']['cold_cap']}")
    print(f"  Raster: {video['width']}x{video['height']}; fit={video['fit']}")
    print(f"  Exact output frames: {output_frames if output_frames is not None else 'not counted'}")
    if output_frames is None and part_count == 1:
        print("  Frame count: deferred until frame extraction")
    if total_frames is not None and source_bounds is not None:
        print(f"  Full timeline frames: {total_frames}")
    if source_bounds is not None:
        print(f"  Selected source range: [{source_bounds[0]},{source_bounds[1]})")
    if part_count is None:
        selected_parts = profile.data.get("multipart", {}).get("parts")
        print(f"  Parts: {selected_parts if selected_parts is not None else 'single profile workflow'}")
    else:
        print(f"  Parts: {part_count}")
        print(f"  Resolved parts: {part_count}")
    if output_frames is not None and part_count is not None:
        from multipart import split_frames
        offset = source_bounds[0] if source_bounds is not None else 0
        ranges = split_frames(output_frames, part_count, offset)
        print(f"  Frame ranges: {ranges}")
        if max(end-start for start, end in ranges) > 65535:
            print("  WordBuf: disabled for any part above 65,535 frames")
    print(f"  Artifact stem: {profile.artifact_stem}")
    if action in ("convert", "disc", "multipart"):
        default_label = ("CAVC_MULTIPART" if part_count and part_count > 1
                         else "SCFMV_DLT")
        label = profile.data.get("release", {}).get("disc_label", default_label)
        if disc_label is not None:
            label = encode_config.normalize_disc_label(disc_label)
        print(f"  ISO volume label: {label}")
    print(f"  Packed artifacts: {profile.artifact_dir}")
    try:
        sim_workspace = profile.output_dir
    except OSError:
        # Planning a profile must remain possible before a later stage reports
        # a missing source; resolving the content-addressed cache can stat it.
        sim_workspace = profile.data["output"]["directory"]
    print(f"  Sim workspace: {sim_workspace}")
    if iso_path is not None:
        print(f"  ISO: {iso_path}")
    elif action == "encode":
        print("  ISO: not requested (encode-only)")
    else:
        print("  ISO: not selected by this profile workflow")
    print("  Encode runtime estimate: unavailable")


MODE_DESCRIPTIONS = {
    "doctor": "Check Python, FFmpeg, native tools, storage, and CUDA.",
    "encode": "Encode a video or profile; do not build a disc image.",
    "disc": "Build a disc image from an existing profile and encoded artifacts.",
    "convert": "Encode a video or profile, then build its disc image.",
    "multipart": "Encode or assemble parts from a profile.",
}


def print_mode_list() -> None:
    print("Sega CD video conversion modes:")
    for name, description in MODE_DESCRIPTIONS.items():
        print(f"  {name:<10} {description}")
    print("\nShow one mode's options and setting values with:")
    print("  tools\\cavc.ps1 help <mode>")
    print("\nExamples:")
    print("  tools\\cavc.ps1 help convert")
    print("  tools\\cavc.ps1 help multipart")


def print_paged(text: str) -> None:
    """Page long interactive mode help so it remains readable in PowerShell."""
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        sys.stdout.write(text)
        return
    lines = text.splitlines(keepends=True)
    page_size = max(1, shutil.get_terminal_size((100, 24)).lines - 1)
    offset = 0
    while offset < len(lines):
        end = min(offset + page_size, len(lines))
        sys.stdout.write("".join(lines[offset:end]))
        sys.stdout.flush()
        offset = end
        if offset < len(lines):
            try:
                response = input("-- More -- (Enter: next page, q: quit) ")
            except EOFError:
                return
            if response.strip().lower() == "q":
                return


def print_help_entry(prefix: str, value: str, width: int) -> None:
    print(textwrap.fill(value, width=width, initial_indent=prefix,
                        subsequent_indent=" " * len(prefix)))


def print_profile_reference(width: int) -> None:
    from comparison_layout import PANEL_CONFIG_KEYS, SLOTS
    from palette_algorithms import PALETTE_ALIASES
    from video_geometry import RESIZE_FILTERS

    print("\nValid --set profile keys (enum choices appear beside the key):")
    nested = {
        ("source", "preprocess"): (
            "source.preprocess.auto_range",
            "source.preprocess.endpoint_snap.black_max",
            "source.preprocess.endpoint_snap.white_min",
        ),
        ("comparison", "panels"): tuple(
            f"comparison.panels.<name>.{key}"
            for key in sorted(PANEL_CONFIG_KEYS)),
    }
    enum_values = {
        ("video", "fit"): "pad, crop",
        ("video", "resize_filter"): ", ".join(sorted(RESIZE_FILTERS)),
        ("video", "output_dither"): "bayer, edge-attenuated-bayer, none",
        ("palette", "algorithm"): ", ".join(sorted(PALETTE_ALIASES)),
    }
    for section, keys in sorted(encode_config.ALLOWED.items()):
        paths = []
        for key in sorted(keys):
            path_values = nested.get((section, key), (f"{section}.{key}",))
            for path in path_values:
                enum = enum_values.get((section, key))
                if section == "comparison" and key == "panels":
                    if path.endswith(".slot"):
                        enum = ", ".join(SLOTS)
                    elif path.endswith(".resize"):
                        enum = ", ".join(sorted(RESIZE_FILTERS))
                paths.append(f"{path} ({enum})" if enum else path)
        print_help_entry(f"  {section}: ", ", ".join(paths), width)
    print("  (Use --set section.key=TOML_LITERAL; strings need TOML quotes.)")


def print_mode_help(parser: argparse.ArgumentParser, name: str, width: int) -> None:
    subparsers = next(action for action in parser._actions
                      if isinstance(action, argparse._SubParsersAction))
    if name not in MODE_DESCRIPTIONS:
        print(f"Unknown mode: {name}")
        print_mode_list()
        raise SystemExit(2)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        print(f"{name}: {MODE_DESCRIPTIONS[name]}\n")
        subparsers.choices[name].print_help()
        if name in {"encode", "convert"}:
            print("\nFPS and cold-cap:")
            for detail in (
                    "--fps RATE selects output cadence; RATE may be rational, such as 24000/1001.",
                    "--cold-cap VALUE limits cold-pattern work per displayed frame, in patterns.",
                    'Supply both options together. Direct video defaults to 15 FPS / 380; profile-only input keeps the profile pair unless both are overridden.',
                    'VALUE may be one positive integer (for example 380) or a cadence map, for example "2:180,3:230" for a 24 FPS 2/3-VBlank cadence.',
                    "The cap is a per-frame transfer limit, not a memory or whole-movie budget."):
                print_help_entry("  ", detail, width)
            print("\nRecommended starting pairs (check each source and playback):")
            for detail in (
                    "15 FPS: --fps 15 --cold-cap 380 (direct-video default)",
                    '24 FPS: --fps 24 --cold-cap "2:180,3:230"',
                    "30 FPS: --fps 30 --cold-cap 200"):
                print_help_entry("  ", detail, width)
            print("\n`--set` profile keys and enum values:")
            print_profile_reference(width)
    print_paged(output.getvalue())


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    terminal_width = max(80, min(shutil.get_terminal_size((100, 24)).columns, 200))
    formatter = lambda prog: argparse.HelpFormatter(prog, width=terminal_width)
    parser = argparse.ArgumentParser(
        description="Convert video sources to Sega CD disc images.",
        formatter_class=formatter)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help=MODE_DESCRIPTIONS["doctor"],
                   formatter_class=formatter)
    for action in ("encode", "disc", "convert", "multipart"):
        cmd = sub.add_parser(action, help=MODE_DESCRIPTIONS[action],
                             description=MODE_DESCRIPTIONS[action],
                             formatter_class=formatter)
        cmd.add_argument("input", type=Path, nargs="?",
                         help="TOML profile or supported video source (default: none; an input is required)")
        cmd.add_argument("--source", type=Path,
                         help="video source path (default: not supplied)")
        cmd.add_argument("--config", type=Path,
                         help="optional TOML profile for --source (default: not supplied)")
        cmd.add_argument("--device", choices=("cpu", "auto", "cuda"), default="auto",
                         help="encoder device selection (default: auto)")
        cmd.add_argument("--debug", action="store_true",
                         help="build a DEBUG disc image with diagnostics (default: off)")
        cmd.add_argument("--region", choices=("jp", "us"), default="jp",
                         help="Sega CD release region (default: jp)")
        if action in ("disc", "convert", "multipart"):
            cmd.add_argument("--disc-label", dest="disc_label",
                             help="ISO volume label, 1..32 ASCII letters, digits, or underscores (default: profile or workflow default)")
        if action == "multipart":
            cmd.add_argument("--parts", type=int,
                             help="override profile part count, 1..64 (default: profile value; must be supplied if absent there)")
            cmd.add_argument("--part", type=int,
                             help="encode only this 1-based part number (default: all parts)")
            cmd.add_argument("--build-only", action="store_true",
                             help="assemble the disc from completed parts only (default: off)")
        elif action in ("encode", "convert"):
            cmd.add_argument("--force", action="store_true",
                             help="ignore a matching completed encode (default: off)")
            cmd.add_argument("--fps", metavar="RATE",
                             help="output frames per second; rational rates accepted (default: 15 for direct video, profile value otherwise)")
            cmd.add_argument("--cold-cap", metavar="VALUE",
                             help='per-frame cold-pattern limit; see timing notes below (default: 380 for direct video, profile value otherwise)')
            cmd.add_argument("--dry-run", action="store_true",
                             help="show the resolved plan without starting stages (default: off)")
            cmd.add_argument("--width", type=int,
                             help="output width in pixels (default: 320 for source-only input, profile value otherwise)")
            cmd.add_argument("--height", type=int,
                             help="output height in pixels (default: 224 for source-only input, profile value otherwise)")
            cmd.add_argument("--fit", choices=("pad", "crop"),
                             help="preserve the whole source or crop to fill (default: pad for source-only input, profile value otherwise)")
            cmd.add_argument("--duration",
                             help="source duration in seconds (default: FFprobe for source-only input; configured profile duration when present)")
            cmd.add_argument("--start-frame", type=int,
                             help="inclusive start frame after FPS conversion (default: beginning of source/profile range)")
            cmd.add_argument("--end-frame", type=int,
                             help="exclusive end frame after FPS conversion (default: end of source/profile range)")
            cmd.add_argument("--audio-filter",
                             help="FFmpeg audio filter chain (default: none for direct source, profile value otherwise)")
            cmd.add_argument("--parts", default=None,
                             help="auto or integer 1..64 (default: auto for direct video; profile [multipart].parts if set, otherwise single profile)")
            cmd.add_argument("--set", dest="sets", action="append", default=[],
                             metavar="SECTION.KEY=VALUE",
                             help="set an additional profile value using a TOML literal (default: no overrides)")
            cmd.add_argument("--save-config", type=Path,
                             help="export the resolved profile without overwriting a file (default: no export)")
            cmd.add_argument("--name",
                             help="safe output identity for direct video input (default: derived from input filename)")
    if not argv:
        print_mode_list()
        return 0
    if argv[0] == "help":
        if len(argv) == 1:
            print_mode_list()
            return 0
        if len(argv) != 2:
            print("Usage: tools\\cavc.ps1 help <mode>")
            return 2
        print_mode_help(parser, argv[1], terminal_width)
        return 0
    args = parser.parse_args(argv)
    if args.action in ("encode", "convert") and args.parts is not None:
        if args.parts != "auto":
            try:
                requested_parts = int(args.parts)
            except (TypeError, ValueError):
                parser.error("--parts must be 'auto' or an integer within 1..64")
            if not 1 <= requested_parts <= encode_config.MAX_MULTIPART_PARTS:
                parser.error(
                    f"--parts must be 'auto' or an integer within "
                    f"1..{encode_config.MAX_MULTIPART_PARTS}")
    identity_claim = None
    deferred_profile_write = False
    profile_multipart_parts = None
    source_total_frames = None
    output_frames = None
    part_count = None
    source_info = None
    duration_origin = "profile"
    assignments: dict[str, object] = {}
    for expression in getattr(args, "sets", []):
        if "=" not in expression:
            raise ValueError(f"--set requires section.key=VALUE: {expression!r}")
        key_path, literal = expression.split("=", 1)
        path_parts = key_path.split(".")
        if len(path_parts) < 2 or any(not part for part in path_parts):
            raise ValueError(f"--set requires a dotted configuration key: {key_path!r}")
        if key_path in assignments:
            raise ValueError(f"duplicate CLI assignment: {key_path}")
        try:
            assignments[key_path] = tomllib.loads("value = " + literal)["value"]
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid TOML literal for --set {key_path}: {exc}") from exc
    early_set_paths = set()
    for item in getattr(args, "sets", []):
        if "=" in item:
            early_set_paths.add(item.split("=", 1)[0])
    early_timing = early_set_paths & {"source.fps", "encoder.cold_cap"}
    if early_timing and len(early_timing) != 2:
        parser.error("--set source.fps and --set encoder.cold_cap must be supplied together")
    if ((getattr(args, "fps", None) is None) !=
            (getattr(args, "cold_cap", None) is None)):
        parser.error("--fps and --cold-cap must be supplied together")
    if (getattr(args, "start_frame", None) is None) != (getattr(args, "end_frame", None) is None):
        parser.error("--start-frame and --end-frame must be supplied together")
    named_timing = {key for key, value in (("source.fps", getattr(args, "fps", None)),
                                           ("encoder.cold_cap", getattr(args, "cold_cap", None)))
                    if value is not None}
    if early_timing & named_timing:
        parser.error("duplicate timing assignment between named options and --set")
    named_paths = set(named_timing)
    if getattr(args, "disc_label", None) is not None:
        named_paths.add("release.disc_label")
    for option, key in (("width", "video.width"), ("height", "video.height"),
                        ("fit", "video.fit"), ("duration", "source.duration"),
                        ("start_frame", "source.start_frame"),
                        ("end_frame", "source.end_frame"),
                        ("audio_filter", "source.audio_filter")):
        if getattr(args, option, None) is not None:
            named_paths.add(key)
    duplicates = named_paths & assignments.keys()
    if duplicates:
        parser.error("duplicate CLI assignments: " + ", ".join(sorted(duplicates)))
    if getattr(args, "disc_label", None) is not None:
        assignments["release.disc_label"] = encode_config.normalize_disc_label(
            args.disc_label, "--disc-label")
    if (args.action != "doctor" and
            (args.source is not None or (args.input is not None and
             args.input.suffix.lower() != ".toml")) and "source.path" in assignments):
        parser.error("the direct input owns source.path; remove --set source.path")
    early_timing_values = {}
    for item in getattr(args, "sets", []):
        if "=" not in item:
            continue
        key, literal = item.split("=", 1)
        if key in early_timing:
            try:
                early_timing_values[key] = tomllib.loads("value = " + literal)["value"]
            except tomllib.TOMLDecodeError as exc:
                parser.error(f"invalid TOML literal for --set {key}: {exc}")
    if args.action == "doctor":
        failures = []
        print(f"Python: {sys.version.split()[0]} ({sys.executable})")
        print(f"Platform: {sys.platform}")
        for executable in ("ffmpeg", "ffprobe"):
            located = shutil.which(executable)
            if located is None:
                print(f"{executable}: missing")
                failures.append(f"{executable} is required")
                continue
            try:
                version = subprocess.run(
                    [located, "-version"], check=True, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                ).stdout.splitlines()[0]
            except (OSError, subprocess.SubprocessError, IndexError) as exc:
                print(f"{executable}: {located} (version probe failed: {exc})")
                failures.append(f"{executable} could not be run")
            else:
                print(f"{executable}: {version} [{located}]")
        print("m68k tools:")
        for name in ("as", "ld", "objcopy"):
            try:
                print(f"  {name}: {build_disc.tool(name)}")
            except FileNotFoundError as exc:
                print(f"  {name}: {exc}")
                failures.append(str(exc))
        try:
            print(f"  nm (multipart): {build_disc.tool('nm')}")
        except FileNotFoundError as exc:
            print(f"  nm (multipart only): {exc}")
        workspace = Path(os.environ.get("CAVC_WORKSPACE_ROOT", ROOT/"tmp/workspace"))
        scratch = Path(os.environ.get("CAVC_SCRATCH_ROOT", workspace/"scratch"))
        for label, selected in (("workspace", workspace), ("scratch", scratch)):
            try:
                selected.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    prefix=".cavc-doctor-", dir=selected,
                ) as probe:
                    probe.write(b"CAVC storage probe\n")
                    probe.flush()
                    os.fsync(probe.fileno())
                usage = shutil.disk_usage(selected)
            except OSError as exc:
                print(f"{label}: not writable ({selected}): {exc}")
                failures.append(f"{label} is not writable: {selected}")
            else:
                print(f"{label}: writable ({selected}); "
                      f"{usage.free / 1024**3:.1f} GiB free")
        try:
            import numpy
            import PIL
            print(f"NumPy: {numpy.__version__}; Pillow: {PIL.__version__}")
        except Exception as exc:
            failures.append(f"Python dependencies unavailable: {exc}")
            print("Python dependencies: error:", exc)
        try:
            print("CUDA:", "available" if gpu_quant.enabled() else "unavailable (CPU fallback)")
        except Exception as exc:
            print("CUDA: error:", exc)
        if failures:
            print("Prerequisite issues:\n- " + "\n- ".join(failures), file=sys.stderr)
            return 1
        return 0

    if args.input is not None and (args.source is not None or args.config is not None):
        parser.error("positional input cannot be combined with --source or --config")
    selected_input = args.config or args.source or args.input
    if selected_input is None:
        parser.error("provide a profile or source video")
    profile_path = selected_input.expanduser().resolve()
    source_override = args.source.expanduser().resolve() if args.source is not None else None
    direct_source_mode = source_override is not None or profile_path.suffix.lower() != ".toml"
    if getattr(args, "name", None) and not direct_source_mode:
        parser.error("--name is available only for direct video input")
    cli_modified_profile = (source_override is not None or getattr(args, "fps", None) is not None
                            or getattr(args, "cold_cap", None) is not None or getattr(args, "width", None) is not None
                            or getattr(args, "height", None) is not None or getattr(args, "fit", None) is not None
                            or getattr(args, "duration", None) is not None or getattr(args, "start_frame", None) is not None
                            or getattr(args, "audio_filter", None) is not None
                            or bool(getattr(args, "sets", [])))
    output_width = getattr(args, "width", None) if getattr(args, "width", None) is not None else 320
    output_height = getattr(args, "height", None) if getattr(args, "height", None) is not None else 224
    output_fit = getattr(args, "fit", None) or "pad"
    if profile_path.suffix.lower() == ".toml":
        selected_fps = getattr(args, "fps", None)
        selected_cold_cap = getattr(args, "cold_cap", None)
        if selected_fps is not None or selected_cold_cap is not None:
            if (selected_fps is None) != (selected_cold_cap is None):
                raise ValueError("--fps and --cold-cap must be supplied together")
            profile = encode_config.load_profile(profile_path)
            resolved_data = copy.deepcopy(profile.data)
            resolved_data["source"]["fps"] = selected_fps
            resolved_data["encoder"]["cold_cap"] = selected_cold_cap
            profile = encode_config.validate_profile_data(
                profile_path, {"schema_version": encode_config.SCHEMA_VERSION,
                               **resolved_data})
        else:
            profile = encode_config.load_profile(profile_path)
        if getattr(args, "width", None) is not None or getattr(args, "height", None) is not None or getattr(args, "fit", None) is not None:
            base = copy.deepcopy(profile.data)
            if getattr(args, "width", None) is not None:
                base["video"]["width"] = args.width
            if getattr(args, "height", None) is not None:
                base["video"]["height"] = args.height
            if getattr(args, "fit", None) is not None:
                base["video"]["fit"] = args.fit
            profile = encode_config.validate_profile_data(
                profile_path, {"schema_version": encode_config.SCHEMA_VERSION, **base})
        if getattr(args, "duration", None) is not None or getattr(args, "start_frame", None) is not None or getattr(args, "audio_filter", None) is not None:
            base = copy.deepcopy(profile.data)
            if getattr(args, "duration", None) is not None:
                base["source"]["duration"] = args.duration
            if getattr(args, "start_frame", None) is not None:
                base["source"]["start_frame"] = args.start_frame
                base["source"]["end_frame"] = args.end_frame
            if getattr(args, "audio_filter", None) is not None:
                base["source"]["audio_filter"] = args.audio_filter
            profile = encode_config.validate_profile_data(
                profile_path, {"schema_version": encode_config.SCHEMA_VERSION, **base})
        if assignments:
            resolved = apply_set_values(profile.data, assignments)
            profile = encode_config.validate_profile_data(
                profile_path, {"schema_version": encode_config.SCHEMA_VERSION, **resolved})
        if source_override is not None:
            from source_inspection import probe, source_duration
            from video_geometry import raw_filter, parse_ratio
            source_info = probe(source_override)
            source_data = copy.deepcopy(profile.data)
            source_data["source"]["path"] = str(source_override)
            duration_origin = ("CLI --duration" if args.duration is not None else
                               "config profile")
            if not source_data["source"].get("duration"):
                duration = source_duration(source_info)
                if not duration:
                    raise ValueError(f"source duration is unavailable for {source_override}")
                source_data["source"]["duration"] = str(duration)
                duration_origin = "FFprobe"
            source_data["source"].setdefault(
                "sar", source_info["video"].get("sample_aspect_ratio", "1:1"))
            if args.fps is None and args.cold_cap is None and not early_timing:
                source_data["source"]["fps"] = "15"
                source_data["encoder"]["cold_cap"] = 380
            elif early_timing:
                source_data["source"]["fps"] = str(early_timing_values["source.fps"])
                source_data["encoder"]["cold_cap"] = early_timing_values["encoder.cold_cap"]
            if assignments:
                source_data = apply_set_values(source_data, assignments)
            source_data = encode_config.validate_profile_data(
                profile_path, {"schema_version": encode_config.SCHEMA_VERSION,
                               **source_data}).data
            geometry = source_data["video"]
            sar_num, sar_den = parse_ratio(source_data["source"].get("sar"))
            count_filter = geometry.get("master_filter") or raw_filter(
                int(geometry["width"]), int(geometry["height"]),
                source_info["display_width"], source_info["display_height"],
                src_sar_num=sar_num, src_sar_den=sar_den,
                fit=geometry.get("fit", "pad"),
                resize_filter=geometry.get("resize_filter", "lanczos"))
            preprocess = source_data["source"].get("preprocess", {})
            snap = preprocess.get("endpoint_snap", {})
            from video_geometry import endpoint_snap_filter
            prefix = endpoint_snap_filter(snap.get("black_max", -1),
                                          snap.get("white_min", 256))
            if prefix:
                count_filter = f"{prefix},{count_filter}"
            source_bounds = (source_data["source"].get("start_frame"),
                             source_data["source"].get("end_frame"))
            selected_parts = getattr(args, "parts", None)
            if selected_parts is None:
                selected_parts = source_data.get("multipart", {}).get("parts")
            if selected_parts is None:
                selected_parts = "auto"
            from multipart import automatic_part_count, split_frames
            part_count = None
            if selected_parts == "auto":
                requested_part_count = None
            else:
                requested_part_count = int(selected_parts)
            defer_frame_count = (requested_part_count == 1 and
                                 source_bounds[0] is None)
            if defer_frame_count:
                part_count = 1
            else:
                from source_inspection import count_output_frames
                source_total_frames = count_output_frames(
                    str(source_override), str(source_data["source"]["fps"]),
                    duration=str(source_data["source"]["duration"]),
                    video_filter=count_filter)
                raw_count_filter = geometry.get("raw_filter") or raw_filter(
                    int(geometry["width"]), int(geometry["height"]),
                    source_info["display_width"], source_info["display_height"],
                    src_sar_num=sar_num, src_sar_den=sar_den,
                    fit=geometry.get("fit", "pad"),
                    resize_filter=geometry.get("resize_filter", "lanczos"))
                if prefix:
                    raw_count_filter = f"{prefix},{raw_count_filter}"
                raw_total_frames = (count_output_frames(
                    str(source_override), str(source_data["source"]["fps"]),
                    duration=str(source_data["source"]["duration"]),
                    video_filter=raw_count_filter)
                    if geometry.get("master_filter") or geometry.get("raw_filter")
                    else source_total_frames)
                if raw_total_frames != source_total_frames:
                    raise ValueError(
                        "master and raw video filters produce different frame counts "
                        f"({source_total_frames} vs {raw_total_frames}); align their timing")
                if source_bounds[0] is None:
                    output_frames = source_total_frames
                else:
                    if source_bounds[1] > source_total_frames:
                        raise ValueError("source frame range exceeds exact output frame count")
                    output_frames = source_bounds[1] - source_bounds[0]
                part_count = (automatic_part_count(output_frames)
                              if requested_part_count is None else requested_part_count)
                if output_frames > part_count * 83520:
                    raise ValueError("selected part count cannot hold the exact output frame count")
                split_frames(output_frames, part_count,
                             source_bounds[0] or 0)
            if not 1 <= part_count <= encode_config.MAX_MULTIPART_PARTS:
                raise ValueError("selected part count must be within 1..64")
            source_bytes = source_byte_identity(source_override)
            if part_count > 1:
                source_data["multipart"] = {"parts": part_count}
            from multipart import toml_text
            from analysis_logs import encoder_version
            source_identity_key = {
                "source": source_bytes,
                "settings": source_data,
                "parts": part_count,
                "encoder_version": encoder_version(),
            }
            source_key = hashlib.sha256(json.dumps(
                source_identity_key, sort_keys=True, ensure_ascii=False,
                default=str, separators=(",", ":")).encode("utf-8")).hexdigest()
            if args.name:
                stem = safe_artifact_name(args.name, source_override)
                source_data["output"]["directory"] = f"tmp/{stem}/sim"
                internal_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.toml"
                identity_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.identity.json"
                identity_claim = (identity_path,
                                  {"schema": 1, "name": args.name,
                                   "source": str(source_override),
                                   "source_sha256": source_bytes["sha256"],
                                   "config_digest": source_key},
                                  named_output_paths(stem))
            else:
                stem = f"{profile.artifact_stem[:135]}-{source_key[:16]}"
                internal_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.toml"
            profile = encode_config.validate_profile_data(
                internal_path, {"schema_version": encode_config.SCHEMA_VERSION,
                                **source_data})
            if not args.dry_run:
                if args.name:
                    deferred_profile_write = True
                else:
                    write_profile_atomic(
                        internal_path,
                        toml_text({"schema_version": encode_config.SCHEMA_VERSION,
                                   **profile.data}))
                    profile = encode_config.load_profile(internal_path)
    else:
        if args.action not in ("encode", "convert"):
            raise ValueError("direct video input is supported by encode and convert")
        if (args.fps is None) != (args.cold_cap is None):
            raise ValueError("--fps and --cold-cap must be supplied together")
        from source_inspection import probe, source_duration
        from multipart import toml_text
        source_info = probe(profile_path)
        fps = (args.fps if args.fps is not None else
               str(early_timing_values["source.fps"]) if early_timing else "15")
        cold_cap = (args.cold_cap if args.cold_cap is not None else
                    early_timing_values.get("encoder.cold_cap", 380))
        duration = (args.duration if args.duration is not None else
                    source_duration(source_info))
        duration_origin = ("CLI --duration" if args.duration is not None else "FFprobe")
        if not duration:
            raise ValueError(f"source duration is unavailable for {profile_path}; supply a config with source.duration")
        safe_stem = safe_artifact_name(args.name, profile_path)
        source_bytes = None
        stem = (safe_stem if args.name else
                f"{safe_stem[:160]}-pending")
        internal_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.toml"
        identity_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.identity.json"
        data = {
            "schema_version": encode_config.SCHEMA_VERSION,
            "source": {"path": str(profile_path), "fps": str(fps), "duration": str(duration),
                       "sar": source_info["display_sar"]},
            "video": {"width": output_width, "height": output_height, "fit": output_fit},
            "output": {"directory": f"tmp/{stem}/sim", "reuse": True,
                       "emit_decisions": True},
            "encoder": {"cold_cap": cold_cap},
            "palette": {"algorithm": "mosaic-gm"},
        }
        if args.start_frame is not None:
            data["source"].update(start_frame=args.start_frame, end_frame=args.end_frame)
        if args.audio_filter is not None:
            data["source"]["audio_filter"] = args.audio_filter
        if assignments:
            data = apply_set_values(data, assignments)
            data = encode_config.validate_profile_data(
                internal_path, {"schema_version": encode_config.SCHEMA_VERSION,
                                **data}).data
            output_width = int(data["video"]["width"])
            output_height = int(data["video"]["height"])
            output_fit = data["video"]["fit"]
            fps = str(data["source"]["fps"])
            cold_cap = data["encoder"]["cold_cap"]
            duration = str(data["source"]["duration"])
            if data["source"].get("start_frame") is not None:
                args.start_frame = data["source"]["start_frame"]
                args.end_frame = data["source"]["end_frame"]
        data = encode_config.validate_profile_data(
            internal_path, {"schema_version": encode_config.SCHEMA_VERSION,
                            **data}).data
        output_width = int(data["video"]["width"])
        output_height = int(data["video"]["height"])
        output_fit = data["video"]["fit"]
        fps = str(data["source"]["fps"])
        cold_cap = data["encoder"]["cold_cap"]
        duration = str(data["source"]["duration"])
        from source_inspection import count_output_frames
        from video_geometry import raw_filter, source_filter, parse_ratio
        from multipart import automatic_part_count
        sar_num, sar_den = parse_ratio(data["source"].get("sar"))
        master_filter = data["video"].get("master_filter") or source_filter(
            output_width, output_height, source_info["display_width"],
            source_info["display_height"], src_sar_num=sar_num,
            src_sar_den=sar_den, fit=output_fit,
            denoise=bool(data["video"].get("master_denoise", True)),
            resize_filter=data["video"].get("resize_filter", "lanczos"))
        raw_frame_filter = data["video"].get("raw_filter") or raw_filter(
            output_width, output_height, source_info["display_width"],
            source_info["display_height"], src_sar_num=sar_num,
            src_sar_den=sar_den, fit=output_fit,
            resize_filter=data["video"].get("resize_filter", "lanczos"))
        source_preprocess = data["source"].get("preprocess", {})
        snap = source_preprocess.get("endpoint_snap", {})
        from video_geometry import endpoint_snap_filter
        prefix = endpoint_snap_filter(snap.get("black_max", -1),
                                      snap.get("white_min", 256))
        if prefix:
            master_filter = f"{prefix},{master_filter}"
            raw_frame_filter = f"{prefix},{raw_frame_filter}"
        part_option = args.parts
        if part_option is None:
            part_option = data.get("multipart", {}).get("parts")
        if part_option is None:
            part_option = "auto"
        if part_option == "auto":
            requested_part_count = None
        else:
            try:
                requested_part_count = int(part_option)
            except ValueError as exc:
                raise ValueError("--parts must be 'auto' or a positive integer") from exc
        defer_frame_count = (requested_part_count == 1 and args.start_frame is None)
        if defer_frame_count:
            part_count = 1
        else:
            source_total_frames = count_output_frames(
                str(profile_path), str(fps), duration=str(duration),
                video_filter=master_filter)
            raw_total_frames = (count_output_frames(
                str(profile_path), str(fps), duration=str(duration),
                video_filter=raw_frame_filter)
                if data["video"].get("master_filter") or data["video"].get("raw_filter")
                else source_total_frames)
            if raw_total_frames != source_total_frames:
                raise ValueError(
                    "master and raw video filters produce different frame counts "
                    f"({source_total_frames} vs {raw_total_frames}); align their timing")
            if args.start_frame is None:
                output_frames = source_total_frames
            else:
                if args.end_frame > source_total_frames:
                    raise ValueError("source frame range exceeds exact output frame count")
                output_frames = args.end_frame - args.start_frame
            part_count = (automatic_part_count(output_frames)
                          if requested_part_count is None else requested_part_count)
        if not 1 <= part_count <= encode_config.MAX_MULTIPART_PARTS:
            raise ValueError(f"resolved part count {part_count} exceeds 1..{encode_config.MAX_MULTIPART_PARTS}")
        if output_frames is not None and output_frames > part_count * 83520:
            raise ValueError(f"{output_frames} frames exceed the selected {part_count}-part format capacity")
        if part_count > 1:
            data["multipart"] = {"parts": part_count}
        from analysis_logs import encoder_version
        from multipart import split_frames
        source_bounds_for_plan = (data["source"].get("start_frame") or 0,
                                  data["source"].get("end_frame"))
        if output_frames is not None:
            split_frames(output_frames, part_count, source_bounds_for_plan[0])
        source_bytes = source_byte_identity(profile_path)
        encode_settings = copy.deepcopy(data)
        encode_settings.get("release", {}).pop("disc_label", None)
        identity_data = {
            "source": source_bytes,
            "encoder_version": encoder_version(),
            "settings": encode_settings,
            "parts": part_count,
        }
        config_key = json.dumps(identity_data, sort_keys=True,
                                separators=(",", ":"), ensure_ascii=False, default=str)
        config_digest = hashlib.sha256(config_key.encode("utf-8")).hexdigest()
        if args.name:
            stem = safe_stem
            internal_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.toml"
            identity_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.identity.json"
            identity_claim = (identity_path,
                              {"schema": 1, "name": args.name,
                               "source": str(profile_path),
                               "config_digest": config_digest},
                              named_output_paths(stem))
        else:
            stem = f"{safe_stem[:145]}-{config_digest[:16]}"
            internal_path = ROOT / "tmp" / "cli_profiles" / f"{stem}.toml"
        data["output"]["directory"] = f"tmp/{stem}/sim"
        profile = encode_config.validate_profile_data(
            internal_path, {"schema_version": encode_config.SCHEMA_VERSION, **data})
        if not args.dry_run:
            if args.name:
                deferred_profile_write = True
            else:
                write_profile_atomic(
                    internal_path,
                    toml_text({"schema_version": encode_config.SCHEMA_VERSION,
                               **data}))
                profile = encode_config.load_profile(internal_path)
    if (profile_path.suffix.lower() == ".toml" and source_override is None
            and args.action in ("encode", "convert")):
        requested_parts = (args.parts if args.parts is not None else
                           profile.data.get("multipart", {}).get("parts"))
        if requested_parts is not None:
            from multipart import automatic_part_count, count_frames
            from source_range import frame_range
            bounds = frame_range(profile.data["source"])
            if requested_parts == "auto":
                requested_part_count = None
            else:
                try:
                    requested_part_count = int(requested_parts)
                except (TypeError, ValueError) as exc:
                    raise ValueError("--parts must be 'auto' or a positive integer") from exc
            defer_frame_count = (requested_part_count == 1 and bounds is None)
            if defer_frame_count:
                profile_multipart_parts = 1
            else:
                source_total_frames = count_frames(profile, profile.artifact_stem)
                output_frames = (source_total_frames if bounds is None else
                                 bounds[1] - bounds[0])
                profile_multipart_parts = (
                    automatic_part_count(output_frames)
                    if requested_part_count is None else requested_part_count)
            if not 1 <= profile_multipart_parts <= encode_config.MAX_MULTIPART_PARTS:
                raise ValueError(
                    f"resolved part count {profile_multipart_parts} exceeds "
                    f"1..{encode_config.MAX_MULTIPART_PARTS}")
            if output_frames is not None and output_frames > profile_multipart_parts * 83520:
                raise ValueError(
                    f"{output_frames} frames exceed the selected "
                    f"{profile_multipart_parts}-part format capacity")
            if output_frames is not None:
                from multipart import split_frames
                split_frames(output_frames, profile_multipart_parts,
                             bounds[0] if bounds is not None else 0)
            if getattr(args, "save_config", None) is not None:
                export_data = copy.deepcopy(profile.data)
                if profile_multipart_parts > 1:
                    export_data["multipart"] = {"parts": profile_multipart_parts}
                else:
                    export_data.pop("multipart", None)
                profile = encode_config.validate_profile_data(
                    profile.path, {"schema_version": encode_config.SCHEMA_VERSION,
                                   **export_data})
    timing_set = {key for key in ("source.fps", "encoder.cold_cap")
                  if key in assignments}
    if timing_set and len(timing_set) != 2:
        raise ValueError("--set source.fps and --set encoder.cold_cap must be supplied together")
    if assignments and not direct_source_mode:
        resolved = copy.deepcopy(profile.data)
        if not args.dry_run:
            from multipart import toml_text
            if profile_path.suffix.lower() == ".toml":
                frozen_key = json.dumps(resolved, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, default=str)
                frozen_stem = (f"{profile_path.stem[:150]}-"
                               f"{hashlib.sha256(frozen_key.encode('utf-8')).hexdigest()[:16]}")
                internal_path = ROOT / "tmp" / "cli_profiles" / f"{frozen_stem}.toml"
            write_profile_atomic(
                internal_path,
                toml_text({"schema_version": encode_config.SCHEMA_VERSION,
                           **profile.data}))
            profile = encode_config.load_profile(internal_path)
    if getattr(args, "save_config", None) is not None:
        from multipart import toml_text
        export_path = args.save_config.expanduser().resolve()
        export_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = toml_text({"schema_version": encode_config.SCHEMA_VERSION,
                                **profile.data})
        try:
            with export_path.open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as exc:
            raise ValueError(f"refusing to overwrite existing config: {export_path}") from exc
        print(f"Saved resolved profile: {export_path}")
    elif (cli_modified_profile and not direct_source_mode
          and profile_path.suffix.lower() == ".toml" and not args.dry_run):
        from multipart import toml_text
        frozen_key = json.dumps(profile.data, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, default=str)
        frozen_stem = (f"{profile_path.stem[:150]}-"
                       f"{hashlib.sha256(frozen_key.encode('utf-8')).hexdigest()[:16]}")
        internal_path = ROOT / "tmp" / "cli_profiles" / f"{frozen_stem}.toml"
        write_profile_atomic(
            internal_path,
            toml_text({"schema_version": encode_config.SCHEMA_VERSION,
                       **profile.data}))
        profile = encode_config.load_profile(internal_path)
    if direct_source_mode:
        fps_origin = ("CLI flags" if args.fps is not None else
                      "--set" if early_timing else 'direct-source default 15/380')
    elif getattr(args, "fps", None) is not None:
        fps_origin = "CLI flags"
    elif early_timing:
        fps_origin = "--set"
    else:
        fps_origin = "profile"
    resolved_part_count = (part_count if direct_source_mode else
                           profile_multipart_parts)
    selected_iso = None
    if args.action in ("convert", "disc"):
        if resolved_part_count is not None and resolved_part_count > 1:
            import multipart
            from disc_region import suffix as region_suffix
            plan_env = dict(os.environ)
            plan_env["CBRSIM_DEVICE"] = args.device
            if args.device == "cpu":
                plan_env["CBRSIM_GPU"] = "0"
            identity = multipart.input_identity(
                profile, resolved_part_count, environ=plan_env)
            multipart_stem = (f"{profile.artifact_stem}-multipart"
                              f"{resolved_part_count}-{multipart.token(identity)[:12]}")
            iso_suffix = region_suffix(args.region) + ("" if args.debug else "_release")
            selected_iso = ROOT / "out" / f"{multipart_stem}{iso_suffix}.iso"
        else:
            from disc_region import suffix as region_suffix
            iso_suffix = region_suffix(args.region) + ("" if args.debug else "_release")
            selected_iso = ROOT / "out" / f"{profile.artifact_stem}{iso_suffix}.iso"
    source_range_value = None
    if profile.data["source"].get("start_frame") is not None:
        source_range_value = (profile.data["source"]["start_frame"],
                              profile.data["source"]["end_frame"])
    print_resolved_plan(
        profile, action=args.action, fps_origin=fps_origin,
        duration_origin=duration_origin,
        source_info=source_info,
        total_frames=source_total_frames, output_frames=output_frames,
        part_count=resolved_part_count, source_bounds=source_range_value,
        iso_path=selected_iso, disc_label=getattr(args, "disc_label", None))
    if getattr(args, "dry_run", False):
        print("Encoder stages: not started; build: not started")
        return 0
    env = dict(os.environ)
    env["CBRSIM_DEVICE"] = args.device
    if args.device == "cpu":
        env["CBRSIM_GPU"] = "0"
    if getattr(args, "force", False):
        env["CBRSIM_FORCE_REENCODE"] = "1"
    lock_stems = profile.lock_stems
    marker = resource_tokens.HELD_STEMS_ENV
    previous = os.environ.get(marker)
    try:
        with resource_tokens.acquire_stems(lock_stems):
            env = resource_tokens.held_stems_environment(lock_stems, env)
            os.environ[marker] = env[marker]
            if identity_claim is not None:
                claim_output_identity(*identity_claim)
            if deferred_profile_write:
                from multipart import toml_text
                write_profile_atomic(
                    internal_path,
                    toml_text({"schema_version": encode_config.SCHEMA_VERSION,
                               **profile.data}))
                profile = encode_config.load_profile(internal_path)
            direct_source = direct_source_mode
            selected_multipart_parts = (part_count if direct_source else
                                        profile_multipart_parts)
            if selected_multipart_parts is not None and selected_multipart_parts > 1:
                command = [sys.executable, str(ROOT/"tools/multipart.py"), str(profile.path),
                           "--parts", str(selected_multipart_parts), "--known-frames", str(source_total_frames),
                           "--security-region", args.region]
                if args.disc_label is not None:
                    command += ["--disc-label", args.disc_label]
                if args.action == "encode":
                    command.append("--encode-only")
                if args.debug:
                    command.append("--debug")
                run_stage("multipart", command, env=env)
                return 0
            if args.action == "multipart":
                command = [sys.executable, str(ROOT/"tools/multipart.py"), str(profile.path),
                           "--security-region", args.region]
                if args.disc_label is not None:
                    command += ["--disc-label", args.disc_label]
                if args.parts is not None:
                    command += ["--parts", str(args.parts)]
                if args.part is not None:
                    command += ["--part", str(args.part)]
                if args.build_only:
                    command.append("--build-only")
                if args.debug:
                    command.append("--debug")
                run_stage("multipart", command, env=env)
                return 0
            if args.action in ("encode", "convert"):
                run_stage("encode", [sys.executable, str(ROOT/"tools/sim.py"), str(profile.path)], env=env)
            if args.action in ("disc", "convert"):
                command = [sys.executable, str(ROOT/"tools/build_disc.py"), str(profile.path),
                           "--region", args.region]
                if args.disc_label is not None:
                    command += ["--disc-label", args.disc_label]
                if args.debug:
                    command.append("--debug")
                run_stage("disc", command, env=env)
    finally:
        if previous is None:
            os.environ.pop(marker, None)
        else:
            os.environ[marker] = previous
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"cavc: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
