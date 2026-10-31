"""Write the small ISO-9660/Joliet-free Sega CD data discs used by CAVC."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import re

SECTOR = 2048


def _both16(value: int) -> bytes:
    return value.to_bytes(2, "little") + value.to_bytes(2, "big")


def _both32(value: int) -> bytes:
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def _date7(path: Path) -> bytes:
    dt = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return bytes((dt.year-1900, dt.month, dt.day, dt.hour, dt.minute, dt.second, 0))


def _record(extent: int, size: int, flags: int, name: bytes,
            date: bytes = bytes((126, 1, 1, 0, 0, 0, 0))) -> bytes:
    pad = b"\0" if len(name) % 2 == 0 else b""
    length = 33 + len(name) + len(pad)
    return (bytes((length, 0)) + _both32(extent) + _both32(size) + date
            + bytes((flags, 0, 0)) + _both16(1) + bytes((len(name),)) + name + pad)


def _directory(records: list[bytes]) -> bytes:
    output = bytearray()
    for record in records:
        if len(output) % SECTOR + len(record) > SECTOR:
            output.extend(bytes(SECTOR - len(output) % SECTOR))
        output.extend(record)
    output.extend(bytes((-len(output)) % SECTOR))
    return bytes(output)


def write_iso(output: Path, boot_image: Path, staged: Path, volume: str) -> None:
    """Create an ISO with the 32 KiB Sega CD boot image at sectors 0..15."""
    boot = boot_image.read_bytes()
    if len(boot) != 16 * SECTOR:
        raise ValueError(f"Sega CD system area must be 32768 bytes, got {len(boot)}")
    entries = list(staged.iterdir())
    if any(not path.is_file() or path.is_symlink() for path in entries):
        raise ValueError("ISO staging may contain only regular files")
    paths = sorted(entries, key=lambda p:p.name.upper())
    if not paths or len(paths) > 128:
        raise ValueError("ISO staging must contain 1..128 regular files")
    for path in paths:
        if (not re.fullmatch(r"[A-Z0-9_]{1,8}(?:\.[A-Z0-9_]{1,3})?", path.name)
                or not path.name.isascii()):
            raise ValueError(f"ISO level-1 filename must use the 8.3 uppercase ASCII form: {path.name}")
    if re.fullmatch(r"[A-Za-z0-9_]{1,32}", volume) is None:
        raise ValueError("ISO volume identifier must be 1..32 ASCII letters, digits, or underscores")
    root_lba = 20
    # Path-table identifiers are padded to an even record length.
    path_bytes = (bytes((1, 0)) + root_lba.to_bytes(4, "little")
                  + (1).to_bytes(2, "little") + b"\0\0")
    path_bytes_be = (bytes((1, 0)) + root_lba.to_bytes(4, "big")
                     + (1).to_bytes(2, "big") + b"\0\0")
    root_records = b""
    lbas = {}
    for _ in range(4):
        root_sectors = max(1, len(root_records) // SECTOR)
        cursor = root_lba + root_sectors
        lbas = {}
        for path in paths:
            lbas[path.name] = cursor
            cursor += max(1, (path.stat().st_size + SECTOR-1) // SECTOR)
        records = [_record(root_lba, max(SECTOR,len(root_records)), 2, b"\0"),
                   _record(root_lba, max(SECTOR,len(root_records)), 2, b"\1")]
        records.extend(_record(lbas[p.name], p.stat().st_size, 0,
                               (p.name + ";1").encode("ascii"), _date7(p)) for p in paths)
        updated = _directory(records)
        if updated == root_records:
            break
        root_records = updated
    else:
        raise RuntimeError("ISO root directory allocation did not converge")
    root_size = len(root_records)
    root_sectors = root_size // SECTOR
    cursor = max(lbas.values()) + max(1, (paths[-1].stat().st_size + SECTOR-1)//SECTOR)
    total = cursor
    pvd = bytearray(SECTOR)
    pvd[0:7] = b"\x01CD001\x01"
    pvd[8:40] = b"CAVC WINDOWS PORT".ljust(32, b" ")
    pvd[40:72] = volume.upper().encode("ascii")[:32].ljust(32, b" ")
    pvd[80:88] = _both32(total)
    pvd[120:124] = _both16(1)
    pvd[124:128] = _both16(1)
    pvd[128:132] = _both16(SECTOR)
    pvd[132:140] = _both32(len(path_bytes))
    pvd[140:144] = (18).to_bytes(4,"little")
    pvd[148:152] = (19).to_bytes(4,"big")
    pvd[156:190] = _record(root_lba, len(root_records), 2, b"\0")[:34]
    pvd[190:318] = b"CAVC".ljust(128, b" ")
    pvd[318:446] = b"CAVC".ljust(128, b" ")
    pvd[446:574] = b"CAVC".ljust(128, b" ")
    pvd[574:702] = b"CAVC".ljust(128, b" ")
    now = datetime.now(timezone.utc)
    volume_date = now.strftime("%Y%m%d%H%M%S00").encode("ascii") + b"\0"
    for offset in (813, 830, 847, 864):
        pvd[offset:offset+17] = volume_date
    pvd[881] = 1
    terminator = bytearray(SECTOR)
    terminator[:7] = b"\xffCD001\x01"
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".pending")
    with pending.open("wb") as out:
        out.write(boot)
        out.write(pvd); out.write(terminator)
        out.write(path_bytes.ljust(SECTOR,b"\0")); out.write(path_bytes_be.ljust(SECTOR,b"\0"))
        out.write(root_records)
        for path in paths:
            position = out.tell()//SECTOR
            if position < lbas[path.name]: out.write(bytes((lbas[path.name]-position)*SECTOR))
            with path.open("rb") as src:
                while block := src.read(1024*1024): out.write(block)
            allocated = max(SECTOR, ((path.stat().st_size + SECTOR-1) // SECTOR) * SECTOR)
            padding = allocated - path.stat().st_size
            if padding: out.write(bytes(padding))
        out.write(bytes((-out.tell()) % SECTOR))
    os.replace(pending, output)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boot-image", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--volume", required=True)
    args = parser.parse_args()
    from encode_config import normalize_disc_label
    args.volume = normalize_disc_label(args.volume, "--volume")
    pending = args.output.with_name(args.output.name + ".verify.pending")
    write_iso(pending, args.boot_image, args.staging, args.volume)
    from multipart_build import verify_iso
    verify_iso(pending, args.staging, disc_label=args.volume)
    os.replace(pending, args.output)


if __name__ == "__main__":
    main()
