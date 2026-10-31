"""Native host CLI for CAVC encode and disc-image production."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="inspect host prerequisites")
    for action in ("encode", "disc", "convert", "multipart"):
        cmd = sub.add_parser(action)
        cmd.add_argument("profile", type=Path)
        cmd.add_argument("--device", choices=("cpu", "auto", "cuda"), default="auto")
        cmd.add_argument("--debug", action="store_true")
        cmd.add_argument("--region", choices=("jp", "us"), default="jp")
        if action == "multipart":
            cmd.add_argument("--parts", type=int)
            cmd.add_argument("--part", type=int)
            cmd.add_argument("--build-only", action="store_true")
        elif action in ("encode", "convert"):
            cmd.add_argument("--force", action="store_true", help="ignore a matching completed encode")
    args = parser.parse_args(argv)
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

    profile = encode_config.load_profile(args.profile)
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
            if args.action == "multipart":
                command = [sys.executable, str(ROOT/"tools/multipart.py"), str(profile.path),
                           "--security-region", args.region]
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
