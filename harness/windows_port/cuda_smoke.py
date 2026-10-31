"""Compile and execute a small CuPy kernel on the selected CUDA device."""
from __future__ import annotations

import math

import cupy as cp


def main() -> None:
    device = cp.cuda.Device()
    values = cp.arange(1, 10_001, dtype=cp.float32)
    result_device = cp.sum(values * values)
    cp.cuda.runtime.deviceSynchronize()
    result = float(cp.asnumpy(result_device))
    expected = float(10_000 * 10_001 * 20_001 // 6)
    relative_error = abs(result - expected) / expected
    name = cp.cuda.runtime.getDeviceProperties(device.id)["name"]
    if isinstance(name, bytes):
        name = name.decode("utf-8", errors="replace")
    print(f"CUDA device: {name}")
    print(f"sum of squares: {result:.1f}; relative error: {relative_error:.3g}")
    if not math.isfinite(result) or relative_error > 1e-6:
        raise SystemExit("CUDA result exceeded the float32 tolerance")


if __name__ == "__main__":
    main()
