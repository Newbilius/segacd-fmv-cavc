"""Build the ordinary CAVC player, packed stream, and Sega CD ISO on any host."""
from __future__ import annotations

import ctypes
import hashlib
import json
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import encode_config
import iso9660
import player_constants
import sp_extension
import av_config
from multipart_build import verify_iso
import resource_tokens


def tool(name: str) -> str:
    prefix = os.environ.get("M68K_PREFIX")
    if not prefix:
        mars = Path(os.environ.get("MARSDEV", str(Path.home() / "toolchains/mars")))
        prefix = str(mars / "m68k-elf" / "bin" / "m68k-elf-")
    candidate = prefix + name
    found = shutil.which(candidate)
    if found:
        return found
    raise FileNotFoundError(f"missing native m68k tool {candidate}; set M68K_PREFIX or MARSDEV")


def _short_windows_native_path(value: str | os.PathLike[str]) -> str:
    """Use an ASCII DOS alias for native tools that cannot decode Unicode argv."""

    path = os.fspath(value)
    if os.name != "nt" or path.isascii():
        return path

    candidate = Path(path)
    suffix: list[str] = []
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return path
        suffix.append(candidate.name)
        candidate = parent

    get_short = ctypes.windll.kernel32.GetShortPathNameW
    get_short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    get_short.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_short(str(candidate), buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        return path
    alias = Path(buffer.value).joinpath(*reversed(suffix))
    result = str(alias)
    if not result.isascii():
        return path
    return result


def _native_m68k_command(
        command: list[str], cwd: Path) -> tuple[list[str], Path]:
    """Shorten Unicode paths only for GNU m68k tools on Windows."""

    if os.name != "nt" or "m68k-elf-" not in Path(command[0]).name.lower():
        return command, cwd

    def translate(argument: str) -> str:
        prefix = ""
        path = argument
        if argument.startswith("-I") and len(argument) > 2:
            prefix, path = argument[:2], argument[2:]
        if not Path(path).is_absolute() or path.isascii():
            return argument
        translated = _short_windows_native_path(path)
        if not translated.isascii():
            raise RuntimeError(
                "native m68k tools cannot read this Unicode path and Windows "
                f"has no ASCII short-path alias: {path}")
        return prefix + translated

    translated = [translate(argument) for argument in command]
    native_cwd = Path(_short_windows_native_path(cwd))
    if not str(native_cwd).isascii():
        raise RuntimeError(
            "native m68k tools cannot use this Unicode working directory "
            f"without a Windows ASCII short-path alias: {cwd}")
    return translated, native_cwd


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    command, cwd = _native_m68k_command(command, cwd)
    print("$ " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def _assemble(source: Path, obj: Path, includes: list[Path], defines: list[str] = ()) -> None:
    obj.parent.mkdir(parents=True, exist_ok=True)
    command = [tool("as"), "-m68000", "--register-prefix-optional", "--bitwise-or"]
    command.extend(f"--defsym={value}" for value in defines)
    command.extend(f"-I{p.as_posix()}" for p in includes)
    command += [source.as_posix(), "-o", obj.as_posix()]
    run(command)


def _link(obj: Path, linker: Path, output: Path) -> None:
    run([tool("ld"), "-nostdlib", "--oformat", "binary", "-T", linker.as_posix(),
         "-o", output.as_posix(), obj.as_posix()])


def _apply_boot_disc_label(boot_bin: Path, label: str) -> None:
    """Copy an explicit ISO label into both fixed-width boot title fields."""
    data = bytearray(boot_bin.read_bytes())
    if len(data) < 0x180:
        raise ValueError("boot image is too short to contain Sega CD title fields")
    title = label.encode("ascii").ljust(48, b" ")
    data[0x120:0x150] = title
    data[0x150:0x180] = title
    boot_bin.write_bytes(data)


def build(profile_path: Path, *, debug: bool = False, region: str = "jp",
          main_codegen: bool = True, player_specialize: bool = True,
          iso_hold_n: int = 0, verify_sp_tail: bool = False,
          pack: bool = True, pack_only: bool = False,
          module_only: bool = False, multipart: bool = False,
          disc_label: str | None = None,
          boot_disc_label: str | None = None):
    if region not in ("jp", "us"):
        raise ValueError("security region must be jp or us")
    profile = encode_config.load_profile(profile_path)
    disc_label = encode_config.normalize_disc_label(
        disc_label if disc_label is not None else
        profile.data.get("release", {}).get("disc_label", "SCFMV_DLT"))
    artifact = ROOT / profile.artifact_dir
    build_dir = ROOT / profile.build_dir
    staging = ROOT / profile.disc_staging_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)

    ext_obj, ext_bin = build_dir / "movieplay_sp_ext.o", build_dir / "movieplay_sp_ext.bin"
    ext_defines = (["MULTIPART=1"] if multipart else [])
    if verify_sp_tail:
        ext_defines.append("ISO_VERIFY_SP_TAIL=1")
    _assemble(ROOT/"boot/movieplay_sp_ext.s", ext_obj, [ROOT/"boot"], ext_defines)
    _link(ext_obj, ROOT/"cfg/sp_ext.ld", ext_bin)
    sp_extension.generate(ext_bin, build_dir/"sp_extension.inc")

    if pack:
        artifact.mkdir(parents=True, exist_ok=True)
        # Remove the complete previous pack before writing any replacement so
        # a failed pack cannot leave stale files looking like this build.
        for name in (
                "HEADER.DAT", "BODY.DAT", "MOVIE.DAT", "paltab.bin",
                "palidx.bin"):
            (artifact / name).unlink(missing_ok=True)
        pack_command = [
            sys.executable, str(ROOT/"tools/pack_stream.py"), "--config",
            str(profile.path), "--sp-extension", str(ext_bin), "--verify",
        ]
        run(pack_command)
    if pack_only:
        return artifact / "HEADER.DAT", artifact / "BODY.DAT"

    constants = build_dir/"player_constants.inc"
    player_constants.generate_include(artifact/"HEADER.DAT", constants)
    shutil.copyfile(ROOT/f"boot/sec_{region}.bin", build_dir/"security.bin")
    shutil.copyfile(ROOT/f"boot/region_{region}.inc", build_dir/"disc_region.inc")
    run([sys.executable, str(ROOT/"tools/gen_debugfont.py"), "--output",
         str(build_dir/"dbgfont.bin")])
    checks = [sys.executable, str(ROOT/"tools/check_player_ring.py"),
              "--constants", str(constants), "--extension", str(ext_bin),
              "--extension-constants", str(build_dir/"sp_extension.inc")]
    if verify_sp_tail:
        checks.append("--sp-tail-marker")
    run(checks)
    run([sys.executable, str(ROOT/"harness/pcm_write_pacing/check_pacing.py")])
    if verify_sp_tail:
        run([sys.executable, str(ROOT/"harness/sp_tail_marker/verify_profile.py"),
             "--header", str(artifact/"HEADER.DAT"), "--max-pending-sectors", "2"])

    base_defines = (["DEBUG=1"] if debug else [])
    if multipart:
        base_defines.append("MULTIPART=1")
    if player_specialize:
        base_defines.append("PLAYER_SPECIALIZED=1")
    ip_defines = base_defines + (["MAIN_CODEGEN=1"] if main_codegen else [])
    ip_obj, sp_obj = build_dir/"movieplay_ip.o", build_dir/"movieplay_sp.o"
    _assemble(ROOT/"boot/movieplay_ip.s", ip_obj,
              [build_dir, artifact, ROOT/"boot"], ip_defines)
    sp_defines = base_defines + ([f"ISO_HOLD_N={iso_hold_n}"] if iso_hold_n else [])
    if verify_sp_tail:
        sp_defines.append("ISO_VERIFY_SP_TAIL=1")
    _assemble(ROOT/"boot/movieplay_sp.s", sp_obj,
              [artifact, build_dir, ROOT/"boot"], sp_defines)
    ip_bin, sp_bin = build_dir/"movieplay_ip.bin", build_dir/"movieplay_sp.bin"
    _link(ip_obj, ROOT/"cfg/ip.ld", ip_bin)
    _link(sp_obj, ROOT/"cfg/sp.ld", sp_bin)
    if ip_bin.stat().st_size > 18944:
        raise ValueError("Main player exceeds 18944 bytes")
    if sp_bin.stat().st_size > av_config.SUB_BOOT_IMAGE_MAX_BYTES:
        raise ValueError(
            f"Sub player exceeds {av_config.SUB_BOOT_IMAGE_MAX_BYTES} bytes")

    if module_only:
        return ip_bin, sp_bin

    boot_obj, boot_bin = build_dir/"movieplay_boot.out", build_dir/"movieplay_boot.bin"
    _assemble(ROOT/"boot/movieplay_boot.s", boot_obj, [build_dir, ROOT/"boot"])
    run([tool("objcopy"), "-O", "binary", str(boot_obj), str(boot_bin)])
    if boot_bin.stat().st_size != 32768:
        raise ValueError(f"complete boot image must be 32768 bytes, got {boot_bin.stat().st_size}")
    if boot_disc_label is not None:
        _apply_boot_disc_label(
            boot_bin, encode_config.normalize_disc_label(boot_disc_label, "--disc-label"))

    for name in list(staging.iterdir()):
        if name.is_dir():
            shutil.rmtree(name)
        else:
            name.unlink()
    (staging/"README.TXT").write_text("delta stream phase B2\n", encoding="ascii", newline="\n")
    shutil.copyfile(artifact/"HEADER.DAT", staging/"HEADER.DAT")
    shutil.copyfile(artifact/"BODY.DAT", staging/"BODY.DAT")
    suffix = ("" if region == "jp" else "_us") + ("" if debug else "_release")
    iso = ROOT/"out"/f"{profile.artifact_stem}{suffix}.iso"
    cue = iso.with_suffix(".cue")
    pending = iso.with_name(iso.name + ".pending")
    iso9660.write_iso(pending, boot_bin, staging, disc_label)
    verify_iso(pending, staging, disc_label=disc_label)
    os.replace(pending, iso)
    metadata = iso.with_suffix(".json")
    hasher = hashlib.sha256()
    with iso.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            hasher.update(block)
    metadata.write_text(json.dumps({"iso": str(iso), "disc_label": disc_label,
                                    "region": region, "debug": debug,
                                    "sha256": hasher.hexdigest()},
                                   sort_keys=True) + "\n", encoding="utf-8")
    (cue.parent).mkdir(parents=True, exist_ok=True)
    cue.write_text(f'FILE "{iso.name}" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n',
                   encoding="ascii", newline="\n")
    return iso, cue


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--region", choices=("jp", "us"), default="jp")
    parser.add_argument("--disc-label")
    parser.add_argument("--no-main-codegen", action="store_true")
    parser.add_argument("--generic-player", action="store_true")
    parser.add_argument("--iso-hold-n", type=int, default=0)
    parser.add_argument("--verify-sp-tail", action="store_true")
    build_mode = parser.add_mutually_exclusive_group()
    build_mode.add_argument("--pack-only", action="store_true")
    build_mode.add_argument("--module-only", action="store_true")
    parser.add_argument("--multipart", action="store_true")
    args = parser.parse_args()
    profile = encode_config.load_profile(args.profile)
    marker = resource_tokens.HELD_STEMS_ENV
    previous = os.environ.get(marker)
    lock_stems = profile.lock_stems
    try:
        with resource_tokens.acquire_stems(lock_stems):
            os.environ.update(resource_tokens.held_stems_environment(lock_stems))
            result = build(
                profile.path, debug=args.debug, region=args.region,
                main_codegen=not args.no_main_codegen,
                player_specialize=not args.generic_player,
                iso_hold_n=args.iso_hold_n, verify_sp_tail=args.verify_sp_tail,
                pack_only=args.pack_only, module_only=args.module_only,
                multipart=args.multipart, disc_label=args.disc_label,
                boot_disc_label=args.disc_label)
    finally:
        if previous is None:
            os.environ.pop(marker, None)
        else:
            os.environ[marker] = previous
    if args.pack_only:
        print(f"verified movie pack: {result[0]}\nBODY: {result[1]}")
    elif args.module_only:
        print(f"verified player modules: {result[0]}\n{result[1]}")
    else:
        iso, cue = result
        print(f"verified player build staged: {iso}\nCUE: {cue}")


if __name__ == "__main__":
    main()
