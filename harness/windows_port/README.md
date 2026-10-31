# Native Windows CLI port plan

This document is the implementation plan and acceptance checklist for the
native Windows conversion CLI. Its commands and components are being added;
their presence does not establish that the complete Windows encode-to-ISO
workflow has been qualified.

The requested outcome is video-to-ISO/CUE conversion on Windows without WSL,
a virtual machine, browser execution, or a mandatory Bash installation. Browser
encoding is excluded because its performance does not meet the user's needs.

Keep the existing Python/NumPy encoder and add native NVIDIA CUDA execution.
Do not rewrite the numerical codec in another language as part of this port.
Preserve Linux support and the active pipeline:

```text
tools/sim.py -> tools/pack_stream.py -> boot/movieplay_*.s
```

## Scope and completion

The completed port must support:

- All six encoder stages: Extract, Palette, Quantize, Forecast, Decide, Finalize.
- Stream packing and its existing verification.
- Player assembly and bootable ISO/CUE generation.
- Explicit CPU and NVIDIA CUDA execution.
- Single-movie and multipart profiles, including individual-part execution.
- Reuse of authenticated completed artifacts without unnecessary re-encoding.
- Both memory-backed and disk-backed numeric arrays.
- DEBUG/release builds and Japan/North America regions.
- Resource coordination, artifact leases, logs, cancellation, and exit status.

Emulator recording, Xvfb, CD-R burning, and publication are separate workflows.
They are not prerequisites for producing a disc image and are outside this port.

Keep target quality fixed: raster, cadence, cold cap, filters, palette policy,
audio format, and all physical scheduling constraints. A lower workload is not
evidence of a repaired implementation or improved performance.

## Repository findings

| Area | Current dependency or concern | Planned treatment |
|---|---|---|
| `tools/resource_tokens.py` | Unconditional `fcntl` import and Unix `flock` | Shared platform-aware file locking |
| `tools/tmpfs_workspace.py` | `fcntl`, PID probes using `os.kill(pid, 0)`, artifact ownership | Portable locks and process identity checks |
| `tools/python.sh` | Bash, `.venv/bin/python`, POSIX `PYTHONPATH` separator | Common Python launcher and PowerShell wrapper |
| `tools/runtime.sh` | Shell-only runtime option handling | Shared runtime configuration |
| `tools/array_storage.py` | Cleanup assumes Unix deletion of open mapped files | Explicit array/view lifetime and Windows-safe cleanup |
| `tools/sim.py` | Multiprocessing and large worker inputs | Qualify actual Windows `spawn` execution |
| `tools/gpu_quant.py` | CUDA initialization and automatic CPU fallback | Qualify CUDA and expose explicit device policy |
| `Makefile` | Shell recipes, m68k tools, ISO tool | Shared Python disc builder |
| `tools/check_player_ring.py` | Build-limit and pack-verification checks read Makefile text | Check the shared Python builder contract |
| `tools/multipart.py` | Calls `make moviepack` | Call shared build implementation |
| `tools/multipart_build.py` | Calls `make movieplay-module`, uses `nm` and ISO tool | Shared player builder and native tools |
| `tools/parallel_run.py` | Shell launchers, Make, Linux recording stages | Native conversion stages and early capability checks |

## Current implementation state

PowerShell bootstrap/launchers, CPU/CUDA device selection, Windows-aware
resource lock primitives, a Python disc-build path, an ISO-9660 writer, and
Windows encode/disc orchestration have been added in the working tree.
The Linux Makefile's `movieplay-internal`, `moviepack`, and `movieplay-module`
targets now call the same Python builder as the Windows CLI; the duplicate
Make recipes for stream packing, player assembly, boot generation, and ISO
creation have been removed. Multipart pack and player-module paths also call
the Python build implementation instead of Make. The parallel runner selects
native Python and the Python disc builder on Windows, and rejects recording/HUD
stages before encoding. The CLI, multipart, and parallel runners share
`tools/process_tree.py` for isolated process-group creation and descendant
termination. POSIX cancellation escalates to SIGKILL after a bounded grace
period; the parallel-run regression includes a child that ignores SIGTERM.
Each workflow retains its own stage/lease receipt handling.
Build entry points also lock both the media-cache identity and the resolved
output path. Windows case normalization rejects case-only output collisions,
and generated profile stems reject reserved device names and leave room for
disc and multipart suffixes.

The native end-to-end path has passed on this Windows host for the canonical
16,384-frame, 320x224, 24 fps source, using disk-backed arrays and unchanged
quality settings. The pinned CPU environment is CPython 3.14.4, NumPy 2.3.5,
and Pillow 12.1.1. The CUDA environment is CPython 3.13.14 with CuPy 14.1.1;
it initialized an NVIDIA GeForce RTX 5060 Ti and executed the actual palette
and quantization paths. CPU and CUDA completed in 1,699.3 s and 1,507.1 s of
stage time (CLI wall times 1,703.7 s and 1,508.2 s). The largest stage,
Decide, remained CPU work. The result is a measured 11.3% lower total stage
time for this source and host, not a general speedup claim.

`harness/runtime_storage/compare.py` found all saved decisions, numeric traces,
palette bytes, masks, and audio outputs identical across CPU and CUDA. The
packed HEADER/BODY/MOVIE files, palette tables, and linked Main/Sub/boot player
binaries also matched byte-for-byte. Native GNU m68k binutils 2.40 built the
real player; the shared builder independently read the JP release ISO and US
DEBUG ISO directory and verified every staged file by size and SHA-256. A
two-part CUDA multipart run encoded frame ranges `[0,8192]` and `[8192,16384]`,
reused part 1 when resuming part 2, and produced a verified menu-free looping
ISO/CUE plus manifest. The `doctor` command reports the installed CUDA device
and m68k toolchain.

The five packed outputs from the Windows CPU run also match the repository's
recorded Linux 8cc7bea baseline exactly; `logs/windows-vs-linux-packed.tsv`
contains the side-by-side hashes. The documented Linux source SHA-256 is
`3e01b37763678a9c2883eec7ded75e71524eba6543478f6d7640b63201699479`, while the
Windows generated source is `3565ea1cdc9c3914c0d9a7e3c01bdacc0f8c032f8210986da72247fd26f64a57`.
Since the original Linux source and stage artifacts are not present here, this
proves stream-byte agreement with that recorded baseline but does not establish
a strict same-normalized-input CPU comparison.

The native player-matrix verifier also passed locally using the exact output of
`install_sgdk_tools.ps1` (pinned SGDK archive SHA-256 above). It assembled and
linked all ten generic/specialized Main/Sub/boot fixtures, including cadence,
centered geometry, audio and pattern-supply variants, and frame-count boundaries
from 16,385 through 83,520. The result was `player constant build matrix: OK`.
This validates the installer/toolchain contract on this host; hosted CI has not
yet run because the workflow changes remain local.
The focused ISO round-trip, competing-process/owner-termination lock,
mapped-array view-lifetime/cleanup, Unicode-path native m68k assembly,
PowerShell argument-boundary, process-tree cancellation, stage-output
visibility, and all eight `test_tmpfs_workspace.py` checks pass. The full-source memory-backed CPU run completed in 1,658.6 s and
matched disk-backed CPU output. A fresh CUDA resource-measurement rerun completed
all 16,384 frames in 1,472.4 s (Extract 29.2 s, Palette 119.1 s, Quantize
218.4 s, Forecast 132.8 s, Decide 972.0 s, Finalize 0.8 s). It used the same
source bytes and effective encoder settings as the earlier CPU/CUDA runs. The
full saved-decision comparison against the previous CUDA artifact passed after
normalizing only profile-file and source-path provenance; numeric traces,
palettes, masks and both audio files were identical.

The output-path lock audit caught and fixed marker ordering that could make a
parent treat its own locks as already inherited. A child-process test now
removes the inherited marker and verifies it cannot enter either parent lock;
the native disc-only workflow passed again with this ordering.

The fresh five-second Windows process-tree sample measured a summed peak
Working Set of 5,737,205,760 bytes (5.34 GiB) and summed
`Get-Process.PrivateMemorySize64` of 1,642,577,920 bytes (1.53 GiB). The earlier
7.38 GiB figure came from `Win32_Process.PrivatePageCount` and is not comparable
to Private Bytes; use the corrected measurement above. Thirty-second recursive
size samples measured workspace peak 91,946,147 bytes (87.7 MiB) and scratch
peak 5,156,372,480 bytes (4.80 GiB). Five-second `nvidia-smi` samples measured
1,162 MiB device-wide GPU use at peak; after the run, idle use was 756 MiB.
GPU memory is device-wide, not attributed to this process. These are sampled
peaks and can miss brief spikes. Raw receipts are
`logs/windows-cuda-resource-16384.{tsv,json}` and
`logs/windows-cuda-vram-16384.{tsv,json}`; output equivalence is in
`logs/windows-gpu-resource-rerun-equivalence.tsv`.

The repository's uv installer downloaded the official 0.11.29 Windows x64
archive, checked SHA-256
`a047d55651bc3e0ca24595b25ec4cfcb10f9dca9fb56514e661269b37d4fae68`, and ran
the resulting binary. Both CPU and CUDA bootstrap scripts then succeeded into
fresh isolated environments with a new uv cache. They installed the pinned
Python/package sets; `doctor` passed with the installed FFmpeg and m68k tools,
and the isolated CUDA environment compiled and ran a CuPy reduction on the
RTX 5060 Ti, synchronized, and copied the result back (relative float32 error
2.81e-9). This verifies clean environment construction on the current host,
not installation on an independent clean machine: its Windows driver, FFmpeg,
and SGDK tools were already available.

A stricter current-host isolation run downloaded and SHA-256-verified the
documented FFmpeg 8.1.2 archive, installed the pinned uv release into a fresh
directory, and downloaded/extracted the hash-pinned SGDK toolchain there. The
official 7-Zip 26.02 installer was also downloaded and matched the recorded
SHA-256; its contents were unpacked with the host's existing 7-Zip into the
isolated bin directory. The isolated 7-Zip then extracted SGDK again, and its
native m68k tools passed version checks. The fresh CPU environment used its
own uv cache and Python 3.14.4 environment, and
`doctor` found only the isolated FFmpeg, m68k tools, NumPy and Pillow. A native
CPU `convert` then encoded 2,798 Tears of Steel frames from the official MOV,
completed all six stages in 486.0 s, packed and verified the stream, assembled
the player, and produced a verified ISO/CUE. A second direct `cavc_cli.py`
conversion started with `PYTHONUTF8` unset, authenticated and reused the
completed encode, then rebuilt the disc successfully. This caught and fixed
UTF-8 propagation for child stages launched directly on Windows. Receipts are
`logs/windows-cli-encode-20261031T061343841448Z.json`,
`logs/windows-cli-disc-20261031T062153178755Z.json`, and
`logs/windows-cli-disc-20261031T062328797070Z.json`; after the isolated 7-Zip
and toolchain refresh, the reuse receipts are
`logs/windows-cli-encode-20261031T062642627880Z.json` and
`logs/windows-cli-disc-20261031T062645288612Z.json`. The final ISO is
18,671,616 bytes with SHA-256
`F76D3DA1B43EC9684EDD17FC34C005844258883BB2B19180AEF612984DD5A89A`.
This proves dependency isolation on the current machine, not an independent
Windows installation; it still uses that machine's Windows image and CUDA
driver. Extraction of the 7-Zip installer itself used the host's preexisting
7-Zip executable.

Reproduce the isolated environment check without replacing `.venv` or
`.venv-gpu`:

```powershell
$env:M68K_PREFIX = Join-Path $PWD 'tmp/windows-toolchain/install-test/bin/m68k-elf-'
$env:UV_CACHE_DIR = Join-Path $PWD 'tmp/windows-clean-bootstrap/uv-cache'
.\harness\windows_port\install_uv.ps1 -InstallDirectory (Join-Path $PWD 'tmp/windows-deps/installed-uv')
$env:PATH = "$(Join-Path $PWD 'tmp/windows-deps/installed-uv');$env:PATH"
.\tools\bootstrap_python.ps1 --cpu -EnvironmentRoot (Join-Path $PWD 'tmp/windows-clean-bootstrap/envs')
.\tools\bootstrap_python.ps1 --gpu -EnvironmentRoot (Join-Path $PWD 'tmp/windows-clean-bootstrap/envs')
& 'tmp/windows-clean-bootstrap/envs/.venv/Scripts/python.exe' tools/cavc_cli.py doctor
& 'tmp/windows-clean-bootstrap/envs/.venv-gpu/Scripts/python.exe' harness/windows_port/cuda_smoke.py
```

An additional real-source run used the repository's Tears of Steel profile and
the official [Blender 720p MOV](https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov)
(SHA-256 `efa9062d9cdb7a338e40ad530dfdf234806743f29ae6a1a136b97ece4e588e8f`).
The film is identified by Blender as CC BY 3.0 on its
[project page](https://mango.blender.org/about/); the downloaded test media is
kept only under ignored `tmp/`. The profile encoded 2,798 frames at 320x168,
24 fps with its existing audio filter, mosaic-gm palette, and 2/3-vblank cold
caps. It completed through the fresh isolated CUDA environment in 461.2 s CLI
wall time (458.2 s stage time). This run exercised three palette segments,
detected fade-in and horizontal scroll, and kept the WordBuf ring enabled
through its complete preload/refill proof. Normal packing verified all display
slots, pattern transfers, audio chunk order, routing and WordBuf deadlines;
native Main/Sub/boot assembly produced `out/tears-of-steel_release.iso` and CUE
in 73.4 s. The independent ISO reader verified staged contents; the ISO is
18,671,616 bytes with SHA-256
`2f956affbd553b82a0c9f7ce412304a36b72d2b14fdad4d8417fa624dc4bc1be`.
Receipts are `logs/windows-cli-encode-20261030T175613680636Z.json` and
`logs/windows-cli-disc-20261030T181448891831Z.json`.

A second real-source variant used `stl4`, 15 fps and cold cap 480. Its 1,749
frames completed in 255.5 s CLI wall time (252.8 s stage time); the WordBuf
ring remained enabled and the full pack/route/audio verification passed. Its
native player/ISO build completed in 37.3 s with exit code 0. The 18,636,800-
byte ISO/CUE is preserved under `tmp/windows-qual-tears-of-steel-stl4-15/`,
SHA-256 `ca86601dccf63671855800f4c1a7da097f415c905fa7d40879e99eda9dc7e530`.
Receipts are `logs/windows-cli-encode-20261030T180930035357Z.json` and
`logs/windows-cli-disc-20261030T181351277754Z.json`. Together, these runs cover
both palette algorithms and the 15/24 fps cadence families on real footage;
they do not cover every source profile or playback.

A third real-source variant used 30 fps, mosaic-gm and cold cap 210. Its 3,498
frames completed in 529.6 s CLI wall time (526.9 s stage time). Packing
verified every display slot, pattern transfer, audio chunk, cadence route and
WordBuf deadline; native player/ISO assembly and independent ISO verification
completed in 99.4 s. The 18,692,096-byte ISO/CUE is preserved under
`tmp/windows-qual-tears-of-steel-30fps/`, SHA-256
`2f109ff954cd7e2d5e1ae2a2c61f3a392ef767e76ece13675c85bf0a737acc89`.
Receipts are `logs/windows-cli-encode-20261030T181756051248Z.json` and
`logs/windows-cli-disc-20261030T182650367454Z.json`.

A fourth variant used the exact project cadence `30000/1001` (29.97 fps),
mosaic-gm and cold cap 210. Its 3,494-frame CUDA encode took 531.8 s CLI wall
time (529.0 s stage time). Packing verified every display slot, pattern
transfer, audio chunk, cadence route and WordBuf deadline; native player/ISO
assembly and independent ISO verification completed in 102.0 s. Its 18,669,568-
byte ISO/CUE are preserved under
`tmp/windows-qual-tears-of-steel-2997/`, SHA-256
`a778ae48d7f84e4ffba97fc05329ecc19f4446f051fdf708472a27dc21265023`.
Receipts are `logs/windows-cli-encode-20261030T185819361995Z.json` and
`logs/windows-cli-disc-20261030T185822110872Z.json`. The documented
`tools/cavc.ps1 convert` command then authenticated and reused that same encode
(`simulation skipped`) and rebuilt the verified ISO in 102.0 s through the
argument-preserving PowerShell launcher. Across these runs both
palette algorithms and 15/24/30/30000/1001 fps cadence families passed on real
footage; other source/profile combinations and playback remain outside this
evidence.

To qualify non-ASCII checkout paths, the tracked working tree was copied to
`tmp/windows-acceptance path_日本_2/checkout`, then both pinned CPU and CUDA
environments were bootstrapped there. `doctor` passed from that checkout and
the documented `cavc.ps1 convert` command reused the existing 3,494-frame CUDA
artifact (3.0 s encode stage; simulation skipped). Packing, native player
assembly and independent ISO verification completed in 102.7 s. The resulting
18,669,568-byte ISO has SHA-256
`de4e1eaddd0daefbca5af657dc896e8e6cabb5d9fac44782876574a2d6966d70`.
The builder passed ASCII DOS short-path aliases to GNU m68k tools, which
otherwise replaced Japanese characters in their input paths with `?`.

The Windows batch orchestrator also ran
`tools/parallel_run.py --through disc --sequential` on the completed 24-fps
profile. It authenticated and reused the sim artifact (4.3 s), then built and
verified the DEBUG ISO/CUE through the native player path (disc stage 74.6 s;
job 78.9 s). `logs/parallel-run-windows-tears-real/summary.tsv` records PASS;
the ISO is `out/tears-of-steel.iso` (18,671,616 bytes, SHA-256
`3493bb2a1a630a467b7b895da6fedf6ee1c573746fd6e0e4e3d0f85b22ff0d49`).

A fresh `cavc.ps1 convert` run then used the documented Gyan FFmpeg/FFprobe
8.1.2 package (archive SHA-256 in the dependency inventory), the pinned GPU
Python environment, the same 2,798-frame Tears of Steel source and unchanged
24-fps profile settings. In its isolated workspace and scratch, all six stages
completed in 427.5 s (CLI receipt 430.3 s); full stream packing, player assembly,
and independent ISO verification completed successfully in a further 72.9 s.
The release ISO is 18,671,616 bytes with SHA-256
`179401e4b1d6b47ef2a6d8eb620f13177c006338294f1f66da6154fb8278e132`.
Receipts are `logs/windows-cli-encode-20261031T044106742057Z.json` and
`logs/windows-cli-disc-20261031T044817023471Z.json`.

After adding the output-path lock and correcting lock-marker order, a disc-only
run reused the same completed CUDA artifact and completed pack verification,
player assembly, and ISO verification in 71.2 s. Receipt:
`logs/windows-cli-disc-20261031T055451945550Z.json`. The 18,671,616-byte ISO
SHA-256 is
`93ebd7b0c961d277532a46ca40cb19a0578744113cbffb45fb195997fb650a53`.

The shared builder's `--module-only` and `--pack-only` paths were then run
against the authenticated 2,798-frame CUDA artifact without starting `sim.py`.
The module path repeated full packing verification, the player-ring and PCM
pacing checks, and real Main/Sub assembly. The pack-only path passed the same
stream checks. Its HEADER and BODY SHA-256 values match both the prior archived
ISO extraction and the pinned-FFmpeg ISO extraction. GNU Make is not installed
on this Windows host, so the Makefile recipes themselves were not invoked here.

`harness/runtime_storage/compare.py` compared this output with the earlier
full-source run after normalizing only profile/source path provenance. All
2,798 saved decisions, numeric traces, palettes, masks, and both WAV format/
PCM sample streams were identical; only the PCM WAV's non-audio RIFF `Lavf`
version text differed. The ISO was extracted with 7-Zip, and `HEADER.DAT`,
`BODY.DAT`, and `README.TXT` matched the archived earlier ISO byte-for-byte.
The saved comparison is `logs/windows-ffmpeg-version-equivalence.tsv`.
This verifies the documented FFmpeg package through the complete native CLI
and confirms equivalent output for this source/profile across the two builds.

The full 16,384-frame source generator now emits a TOML-safe absolute Windows
path. A regenerated source under a workspace path containing spaces and
Japanese characters was parsed, encoded with CUDA, packed, and built into a
verified ISO. Its normalized 320x224 YUV420P frame stream and 22.05 kHz mono
PCM hash-match the earlier Windows source even though the MP4 container hashes
differ; `logs/windows-normalized-source-equivalence.tsv` records both inputs.
The two full Windows decision logs, numeric traces, palettes, masks, and audio
PCM also match (`logs/windows-ffmpeg-source-16384-equivalence.tsv`). The five
packed files from the regenerated run match the recorded Linux baseline hashes
(`logs/windows-linux-normalized-16384.tsv`). The original Linux MP4 is absent,
so its decoded stream cannot be independently hashed here; both source files
were generated by the same lavfi signal/filter recipe, with lossless x264 video
and a silent AAC track. The CUDA encode completed in 1,489.2 s and the resumed
disc-only build in 214.2 s. Receipts are
`logs/windows-cli-encode-20261031T050842494098Z.json` and
`logs/windows-cli-disc-20261031T053345618897Z.json`; the release ISO SHA-256 is
`42275F1B4168852065D90676AF148650100F40A39C8B0722D499410ED4090725`.

To compare sources whose container metadata differs, run the decoded-stream
comparator with the pinned Windows FFmpeg tools:

```powershell
tools/python.ps1 harness/windows_port/compare_normalized_source.py `
  path\to\baseline.mp4 path\to\candidate.mp4 `
  --ffmpeg path\to\ffmpeg.exe --ffprobe path\to\ffprobe.exe `
  --tsv logs/normalized-source.tsv
```

It checks dimensions, frame rate and frame count, then hashes the complete
YUV420P video and normalized mono 22.05 kHz PCM audio streams. Matching stream
hashes establish decoded-media equivalence, not equality of the input
containers.

The recorded historical Linux MP4 remains unavailable for direct decoded-stream
comparison. The optional full workflow now creates one Linux 16,384-frame MP4,
passes those exact bytes to Windows, checks normalized decoded video/audio,
compares every saved decision and numeric trace, and compares all five packed
files after the Linux Make build. That workflow is implemented but has not run
on hosted runners yet. Remaining acceptance gaps are hosted Windows/Linux CI
evidence, clean-machine bootstrap evidence, actual execution of the Linux Make
targets, and broader real-source coverage across remaining profile/source
combinations.
The flat-color source still disables the WordBuf ring after its exact full-run
preload proof reports `(0, 0)` instead of `(3072,3200)`; the Tears of Steel run
now provides an active-ring full-source qualification. Do not claim the port
fully accepted until the remaining checks below have evidence.

The official SGDK v2.11 Windows release archive is pinned by SHA-256
`5cc704b7e3a15183c33e721a1d7f84c067cf78808754556d03bb14764df51437`. The
included GNU m68k binutils 2.40 were installed and used to assemble the real
Sub extension and specialized Main/Sub player modules (linked sizes 1,108,
10,624, and 4,032 bytes). Player modules used a synthetic HEADER fixture and
zero-filled palette tables to exercise native assembly/linking; this is not a
valid movie or bootable disc qualification. The install script verifies the
archive hash before extraction and prefixes/version-checks the four conversion
tools (`as`, `ld`, `objcopy`, `nm`) plus the `size` and `objdump` utilities used
by the player matrix.

### Windows external-tool inventory

These are the documented x64 setup inputs as of 2026-10-31. WinGet verifies
the package hashes in its manifests; the repository verifies the uv and SGDK
archives directly. Python, NumPy, Pillow, CuPy, and the CUDA runtime packages
are pinned in `.python-version`, `.python-version-gpu`, `pyproject.toml`, and
`uv.lock`.

| Tool | Version and source | SHA-256 / verification | License information |
|---|---|---|---|
| uv | 0.11.29, official Astral Windows x64 release; `install_uv.ps1` | `A047D55651BC3E0CA24595B25EC4CFCB10F9DCA9FB56514E661269B37D4FAE68`; checked by installer | MIT or Apache-2.0; license texts are available from Astral's repository |
| FFmpeg / FFprobe | 8.1.2, Gyan full Windows x64 build via WinGet `Gyan.FFmpeg` | `B8CDEFAB5F50590A076C27C2B56B0294A0E6154FADED28BA1BA05EBC4F801F57`; WinGet manifest and local download verified | GPL-3.0 full build; see package and upstream build license notices |
| 7-Zip | 26.02 x64 via WinGet `7zip.7zip` | WinGet x64 MSI: `DB407A4F6D4999E5C7BC00CE8A882BE94717B56E7FA68140FE3F12605D91643E`; standalone x64 EXE: `6745FA76DC2EA031596D8678F6F6B99C3C1B435B4164A63485ADBBC7B8D82EF0` | LGPL, with BSD 3-Clause components and an unRAR restriction |
| GNU m68k binutils | 2.40 from official SGDK v2.11 Windows release | `5CC704B7E3A15183C33E721A1D7F84C067CF78808754556D03BB14764DF51437`; checked by `install_sgdk_tools.ps1` | License and runtime notices are extracted from the release archive |
| NVIDIA driver | Host-installed driver compatible with the CUDA 12 runtime | `doctor` and `cuda_smoke.py` check availability and execution; driver version is host-managed | NVIDIA driver terms apply; it is not bundled by this project |

WinGet selects the Gyan FFmpeg 8.1.2 ZIP and the 7-Zip 26.02 x64 MSI on the
hosted Windows runner; their hashes above are the exact package hashes. The
7-Zip standalone x64 EXE hash is also recorded because the isolated setup
qualification downloaded and checked that installer separately. These are
installer/archive hashes, not hashes of the installed executables. `doctor`
reports the FFmpeg/FFprobe version actually found on `PATH`; use it to record
host deviations from the documented setup.

The Gyan 8.1.2 x64 archive was also downloaded and independently checked
against the recorded hash on this host. Its `doctor` report showed the expected
FFmpeg/FFprobe versions. The binary exposed all profile filters/codecs checked
by the setup smoke (`aformat`, `volume`, `asoftclip`, `alimiter`, `setsar`,
`crop`, `scale`, `guided`, `hqdn3d`, `gblur`, `fps`, `pad`, `libx264`, `aac`,
`pcm_s16le`, and `rawvideo`); two-second Sakura-style video/audio and Sonic Jam
guided-filter graphs decoded successfully. This qualifies tool compatibility,
not a full encode with this package; the recorded full-source runs used the
host's then-installed FFmpeg build.

A two-second synthetic video/audio CPU smoke reached the physical schedule
check after extraction, palette, scroll, and quantization work, then failed the
existing raw-funding invariant (`7968/7929`) without publishing a completion
marker. The later canonical 16,384-frame CPU and CUDA encodes both completed;
the short-input failure remains a separate model edge case and must not be
treated as a successful encode or silently worked around.

The focused cross-platform checks are:

```powershell
tools/python.ps1 -m unittest discover -s harness/windows_port -p test_*.py
```

They check Windows mapped-array lifetime, round-trip ISO output through the
independent reader, exercise cross-process lock release after owner
termination, and round-trip a Windows path containing spaces and Japanese
characters through the generated TOML profile. Run them on Windows and POSIX.
The PowerShell launcher test protects wrapper/child option boundaries, explicit
device selection, CLI-over-environment runtime precedence, child TMP/TEMP
propagation and isolation from the caller's PowerShell environment.

`.github/workflows/windows-cli.yml` runs Linux and Windows jobs. Linux installs
GNU Make and m68k binutils, executes `make check-player-tools`, builds the
generic/specialized player matrix, and runs the cross-platform lock, profile,
process, cache, and mapped-workspace checks. A manual dispatch can also select
`full_linux_make` to generate the canonical 16,384-frame source, run all six CPU
stages, then execute `make moviepack`, `make movieplay-module`, and
`make movieplay-internal` against that completed artifact before independently
checking the ISO against its staged files. It then uploads the source and Linux
comparison artifacts to a dependent Windows job. That job rebuilds a profile
around the exact downloaded MP4, runs a full CPU `convert`, compares normalized
video/audio hashes, all saved decisions and numeric fields, the packed
HEADER/BODY/MOVIE/palette files, and assembled Main/Sub/boot player binaries.
The long qualification is opt-in. Windows also
bootstraps the pinned CPU environment, checks that an
isolated bootstrap preserves the caller's `UV_PROJECT_ENVIRONMENT`, installs
the hash-pinned official m68k toolchain, assembles and
links the real Sub extension, and builds the same player matrix from current
synthetic HEADER fixtures. The matrix covers cadence, geometry, audio,
pattern-supply and maximum frame-index cases; it is a build qualification, not
a real encoded movie or bootable ISO. The Windows job also installs pinned
Gyan FFmpeg 8.1.2 and 7-Zip 26.02, generates a 48-frame synthetic source, runs
all six stages with disk-backed arrays on CPU, and builds and independently
verifies a DEBUG North America ISO/CUE through `cavc.ps1 convert`. It then
encodes two CPU multipart parts in separate invocations, asserts authenticated
reuse of part 1, and independently verifies the looping ISO and manifest ranges.
It also runs focused port checks, parallel-stage and process-tree cancellation
tests, resource-token/output-lock, profile-path/artifact-name, spawn-selection,
and authenticated-artifact-reuse tests, direct-CLI UTF-8 propagation,
launcher device/argument-boundary/exit/UTF-8 checks, PowerShell parsing, and a
CTRL_BREAK cancellation test that verifies the PowerShell launcher exits with a
failure and its Python descendant is gone. The launcher test includes child
arguments with embedded quotes, spaces, an empty value and a trailing
backslash. Hosted jobs have not run yet. The workflow passes local `actionlint`
1.7.12 validation; that checks workflow syntax and expressions, not hosted job
execution.

The same short CPU path passed locally with the pinned FFmpeg and SGDK toolchain:
48 frames completed all six stages in 4.35 s, and native DEBUG US player/ISO
assembly plus independent staged-file verification completed in 1.91 s. The
419,840-byte ISO SHA-256 is
`52aa64f545ff1b2f461117383d209633db10598c706e08c81bd7bf0de5f63ffd`.
Receipts are `logs/windows-cli-encode-20261031T065438836753Z.json` and
`logs/windows-cli-disc-20261031T065443188199Z.json`.

The 48-frame source also passed CPU multipart through two separate
`cavc.ps1 multipart --parts 2 --part K` invocations. Each 24-frame part passed
full packing verification; the second invocation authenticated and reused part
1, encoded part 2, then assembled the looping release ISO. Its manifest records
ranges `[0,24]` and `[24,48]`. The independent ISO reader verified all ten
staged files, and the CUE references the generated image. The 542,720-byte ISO
SHA-256 is
`f7f49cee2dc71ba6a6897364ecebedad456c145cac9a1636a4c91aab98d6e949`.
Receipts are `logs/windows-cli-multipart-20261031T070038195194Z.json` and
`logs/windows-cli-multipart-20261031T070042969840Z.json`.

## Native Windows entry points

The current PowerShell entry points are:

```powershell
uv self update 0.11.29
.\tools\bootstrap_python.ps1 --cpu
.\tools\bootstrap_python.ps1 --gpu

$env:M68K_PREFIX = 'C:\toolchains\m68k-elf\bin\m68k-elf-'
.\tools\cavc.ps1 doctor
.\tools\cavc.ps1 convert profiles\PROFILE.toml --device cuda
.\tools\cavc.ps1 convert profiles\PROFILE.toml --device cpu
.\tools\cavc.ps1 multipart profiles\PROFILE.toml --parts 3
```

`M68K_PREFIX` must name native Windows executables for `as`, `ld`, `objcopy`,
and `nm`; the standard single-disc path uses the first three. The installer
also provides `size` and `objdump` for the optional player-matrix verifier.
GCC is not used to assemble the normal player. FFmpeg and FFprobe must also be
on PATH. Use a short workspace/scratch path with enough free disk space for
long sources.

CPU/CUDA encode, single-disc conversion, DEBUG/region assembly, multipart
resume, and independent ISO verification have passed for the acceptance source
on this host. This is partial host qualification; the remaining comparisons
and environment checks above still prevent a full support claim.

The existing quantization pool already selects `spawn` when `fork` is
unavailable. Linux quota probes already have platform guards. These reduce the
porting work but do not establish native Windows compatibility.

## 1. Freeze the baseline and acceptance inputs

Record the starting commit, public encoder version, effective settings, and tool
versions. Select representative profiles covering both palette algorithms,
supported cadence families, audio, and multipart behavior.

Find existing matching completed artifacts before commissioning new encodes.
Only use reference artifacts whose source identity, settings, encoder version,
and completion state are known. Record checksums and retain independent readers
or comparators where available.

Separate these claims throughout the work:

- Correctness: the produced stream and player are valid.
- Non-regression: the port preserves the reference results and constraints.
- Measured benefit: native execution achieves the observed performance.

**Exit evidence:** a frozen baseline and a documented set of acceptance inputs
with known provenance. New expensive runs have a declared input, output,
expected duration, command, log, and completion evidence.

## 2. Resolve Windows dependency availability first

Before substantial implementation, qualify installation on a clean Windows
environment using the repository pins:

- `uv==0.11.29`.
- CPU CPython 3.14.4 and GPU CPython 3.13.14.
- NumPy 2.3.5 and Pillow 12.1.1.
- CuPy 14.1.1 and the required CUDA components.
- FFmpeg and FFprobe with the actual filters/codecs used by the pipeline.
- Native m68k assembler, linker, objcopy, and multipart symbol inspection tools.
- A native ISO creation tool supporting the required Sega CD boot layout.

Do not assume that an import proves GPU usability. Exercise device allocation,
the compilation/execution paths used by CuPy, synchronization, and result
transfer. Confirm the required driver/runtime combination on actual hardware.

Audit the exact m68k tools needed by conversion targets. The current generic
`check-tools` requires GCC, but the normal movie player is assembly; do not
require a compiler unless the selected target actually uses one. Multipart
requires `nm` in addition to the ordinary assembly tools. Audit verification
commands for any disassembler requirement as well.

Identify tool sources, versions, licenses, and checksums. If a pinned dependency
is unavailable, make an explicit compatibility decision and qualify any version
change independently. Do not silently use system Python or arbitrary packages.

**Exit evidence:** a reproducible dependency installation and successful native
tool probes. Tool availability risks are resolved before relying on them.

## 3. Add a portable launcher and environment bootstrap

Introduce a PowerShell bootstrap and a common Python launcher. Keep PowerShell
thin: environment selection, version checks, runtime option parsing, and child
execution should have one shared implementation where practical.

The launcher must:

- Select the isolated CPU or GPU environment and enforce the selected pins.
- Handle `Scripts/python.exe` on Windows and `bin/python` on POSIX.
- Construct `PYTHONPATH` with `os.pathsep`.
- Preserve CLI-over-environment precedence for runtime storage options.
- Export workspace, scratch, TMPDIR, TMP, and TEMP consistently to children.
- Execute argument lists without shell command concatenation.
- Preserve child exit status and visible progress.
- Preserve documented working-directory and relative-path behavior.

Retain compatible Linux entry points, eventually delegating their common logic
to the new implementation. Avoid maintaining two independent option parsers.

Add a proposed `doctor` command reporting interpreter/package versions,
executable paths, CUDA usability, writable storage, and available disk space.
Report actionable failures before expensive work starts.

**Exit evidence:** installation, diagnosis, imports, and Python tool execution
work from PowerShell, including a checkout path containing spaces.

## 4. Port locks, leases, and process identity

Introduce a small shared locking module with POSIX and Windows backends. Choose
the backend after checking its actual semantics. Locks must coordinate separate
processes and release when their owner exits, including abnormal termination.

Preserve CPU/GPU/EMU tokens, exclusive output-stem locks, immediate same-stem
rejection, nested invocation behavior, shared resource roots, and protection
against eviction of actively leased artifacts. Do not bypass coordination to
make Windows run.

Replace the Windows PID probe with a platform-specific implementation. Account
for permissions and PID reuse; add process-start identity to lease records if
needed to avoid mistaking an unrelated process for the original owner.

**Validation:** real competing processes, owner termination, nested commands,
shared resource roots across workspaces, and eviction attempts against a live
lease. These checks protect actual concurrent jobs rather than private shapes.

**Exit evidence:** coordination and cleanup remain correct under contention and
abnormal termination on Windows and POSIX.

## 5. Make mapped-array and filesystem lifetimes portable

Audit ownership of mapped arrays and every view that can outlive a stage. Flush
and release mappings only after their last consumer is finished, then delete
their temporary files. Do not close a mapping while a live view still needs it.

Cover normal completion, exceptions, cancellation, and transitions between
encoder stages. Preserve numeric results in both `memory` and `disk` modes.

Also audit:

- Atomic metadata/completion publication and replacement of existing files.
- UTF-8 text and LF output for project delimited data.
- Paths containing spaces and non-ASCII characters.
- Generated path lengths and Windows filename restrictions.
- Case-insensitive path collisions and source/profile path resolution.
- Filesystem capacity accounting and optional Linux-only probes.

Unavailable OS metrics must remain explicitly unavailable, not fabricated as
zero. Interrupted outputs must never gain a successful completion marker.

**Exit evidence:** disk-backed encoding and cleanup succeed without deleting
active data or leaving normal-exit temporary arrays behind.

## 6. Port child execution, cancellation, and job observation

Share process execution logic between the new CLI, multipart, and parallel
conversion. Invoke child Python through the selected interpreter rather than a
shell launcher.

Qualify `spawn` using actual workers: import safety, main guards, profile/env
propagation, picklable arguments, and large-array transfer. Measure whether
spawning duplicates large inputs; optimize transfer only after identifying a
real cost.

Implement cancellation of the task-owned process tree, including FFmpeg and
worker descendants, using appropriate platform mechanisms. Never terminate
unrelated jobs. Keep command, PID/process identity, current log, start/end times,
and final exit status observable. Ensure Ctrl+C does not leave an unobserved
encode running.

**Exit evidence:** success, child failure, and cancellation produce correct
receipts, release resources, and stop all owned descendants.

## 7. Qualify native CPU and CUDA encoding

First complete a short real CPU encode through every stage. Then immediately
qualify the GPU path; do not postpone CUDA until after packaging.

Proposed device policy:

- `cpu`: explicitly run on CPU.
- `auto`: use CUDA when available and clearly report any CPU fallback.
- `cuda`: fail if CUDA cannot execute; never silently run a long CPU encode.

Check palette training, quantization, the actual CuPy kernels, worker startup
after parent CUDA initialization, and final synchronization/error reporting.
Print the actual selected execution mode and device in the run log.

**Exit evidence:** complete native CPU and CUDA encodes with fixed quality and
validated outputs. Claims of GPU acceleration include actual device execution.

## 8. Extract the movie-disc build into shared Python code

Introduce a proposed `tools/build_disc.py`, usable by both the Windows CLI and
the Linux Makefile. Keep one implementation of movie-disc construction instead
of independently translating shell recipes for Windows.

Preserve the dependency order:

1. Assemble/link the Sub-CPU extension.
2. Pack the stream with that extension and full normal verification.
3. Generate player constants, tables, fonts, and regional inputs.
4. Assemble/link the Main and Sub players.
5. Enforce memory-map and binary-size limits.
6. Build the complete 32,768-byte boot image.
7. Stage files and generate ISO/CUE.

Retain player ring checks, PCM write pacing, diagnostic flags, specialization,
code generation settings, and DEBUG/region isolation. Some verifiers currently
inspect Makefile text: update them to validate the real build configuration
after extraction without weakening the guarantee.

Qualify the native ISO tool early. Prefer a suitable existing native tool before
introducing an ISO writer. If a new writer is necessary, treat it as a separate
implementation with independent ISO, boot-area, file-extent, and content checks.

**Exit evidence:** build a verified ISO/CUE from a matching completed sim without
re-encoding, Bash, or Make. Build products retain their existing locations and
names unless a documented compatibility decision requires otherwise.

## 9. Integrate multipart and batch conversion

Replace Make calls in `tools/multipart.py` and `tools/multipart_build.py` with
shared packing/player-build operations. Preserve TOML/CLI precedence and route
multipart requests through the multipart workflow.

Preserve individual-part selection, authenticated completion markers, source
frame ranges, reuse, lease handoff, and automatic disc assembly only after all
parts are ready. Preserve menu-free ordered playback and looping with the
existing loading pause between parts.

Adapt `tools/parallel_run.py` through the disc stage. If a user requests a
platform-dependent recording/HUD stage unavailable on Windows, reject it before
starting a costly encode and explain the unsupported capability.

**Exit evidence:** run parts separately, resume with completed parts intact,
and assemble one multipart ISO without repeating valid upstream work. Batch
profiles retain resource-token and output-stem protection.

## 10. Establish equivalence and measure performance

Use checks selected for realistic failures:

| Check | Failure it catches |
|---|---|
| Real cross-process locks and leases | Concurrent writes or eviction of active data |
| Memory/disk output comparison | Mapping lifetime or storage-dependent values |
| Linux/Windows comparison on identical normalized inputs | Numerical encoder regression |
| Separate FFmpeg normalization comparison | Decoder/filter differences misattributed to the encoder |
| Decision, palette, audio, and packed-byte comparison | Changed conversion output |
| Independent ISO reader and extracted-file comparison | Incorrect container layout despite correct packed data |
| Built instructions and matching playback when relevant | Toolchain/player assembly differences |

Use `harness/runtime_storage/compare.py` where applicable. Compare packed
`HEADER.DAT`, `BODY.DAT`, `MOVIE.DAT`, `paltab.bin`, and `palidx.bin` with the same
boot extension and normal pack verification. Compare boot/player binaries too.
Do not use whole-ISO hashes alone: timestamps and container metadata can differ.

Investigate the first divergence across decoded inputs, numeric stages,
decisions, packing, assembly, and playback. Do not loosen checks to conceal a
production failure. A Python copy of assembly is not assembly validation.

For full memory qualification, use the actual 16,384-frame source identified in
`harness/runtime_storage/README.md` and execute all stages. Short clips do not
replace this qualification. Maximum-index and WordBuf boundary checks should
use the existing synthetic fixtures instead of an unnecessary maximum-length
source encode.

Measure stage time, total wall time, process/process-tree memory with explicit
measurement semantics, VRAM, and temporary disk use. Separate cold setup/JIT,
fresh encode, and completed-artifact reuse. Establish performance acceptance
thresholds from baseline measurements; do not invent a speedup target before
measuring. Keep source settings, worker policy, and hardware comparable.

Run relevant checks once, repeating after a pertinent fix or new concern.
Retain commands, fresh logs, receipts, and real exit status for long runs.

**Exit evidence:** explained numerical/stream differences, independently valid
disc output, complete long-source qualification, and measured native performance.

## 11. Deliver installation, CI, and maintained documentation

Add Windows CPU CI for the portable infrastructure and native build. Qualify
CUDA on a real NVIDIA Windows host. Retain relevant Linux checks.

Provide reproducible installation instructions and an inventory of external
tools with versions, checksums, licenses, and acquisition/build instructions.
Decide whether to distribute a portable bundle only after the dependency and
licensing checks; do not make a bundled executable a prerequisite for the port.

Update the existing documentation owners:

- `README.md`: Windows setup, commands, supported workflows, troubleshooting.
- `CONFIG.md`: device policy, runtime options, resource/lease behavior.
- `ENCODE.md`: actual execution behavior and measured timings.
- `harness/test_audit/README.md` and its inventory: validation ownership/costs.
- `AGENTS.md`: accurate environment and maintenance guidance.

Root public Markdown changes must preserve the complete English/Japanese
structure. Keep port diagnostics and supporting scripts under this directory.
Avoid unrelated assembly or stream-format changes. Bump the public encoder
version for output-affecting changes; retain existing reuse authentication.

**Exit evidence:** a clean Windows machine can install dependencies and execute
the documented conversion without undocumented tools or manual source patches.

## Current user interface

These commands are implemented in the working tree; see the qualification
status above before relying on them for a production encode:

```powershell
.\tools\bootstrap_python.ps1 --cpu
.\tools\bootstrap_python.ps1 --gpu

.\tools\cavc.ps1 doctor
.\tools\cavc.ps1 encode profiles\example.toml --device cuda
.\tools\cavc.ps1 disc profiles\example.toml
.\tools\cavc.ps1 convert profiles\example.toml --device cuda
.\tools\cavc.ps1 multipart profiles\example.toml --parts 3 --device cuda
```

`encode` produces completed sim artifacts; `disc` builds from a completed sim;
`convert` coordinates both. `multipart` retains existing part-selection,
completion authentication, and resume semantics. CLI values continue to follow
the profile/runtime precedence defined by the existing project configuration.

## Implementation order and final acceptance

Implement in dependency order:

```text
Dependency qualification
  -> launcher/bootstrap
  -> locks, leases, and mapped storage
  -> processes and cancellation
  -> CPU/CUDA encoding
  -> native player/disc build
  -> multipart and batch integration
  -> equivalence, long-source performance, CI, and delivery
```

The port is complete when a clean Windows host reproducibly produces a
verified ISO/CUE through the native CLI, with real CUDA execution, correct
cancellation, safe completed-artifact reuse, working multipart, and full
long-source qualification. Linux conversion must remain functional.

The implementation is present and has passed substantial host qualification,
but the port remains in progress until the outstanding acceptance evidence near
the top of this document is closed. In particular, this checkout cannot
produce hosted CI evidence without publishing the local workflow changes. The
optional full workflow now generates and shares one source for strict
Linux/Windows comparison, but it has not run. The current-host environment
checks also do not replace a clean-machine install. Keep these evidence limits
explicit when reporting support status.
