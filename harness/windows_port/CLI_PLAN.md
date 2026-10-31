# Direct video CLI implementation plan

This document records the accepted contract and completion evidence for the
direct video-to-ISO/CUE interface. The CLI behavior is implemented in the
working tree; commands below describe the current interface. Public usage is
owned by the root README, configuration semantics by CONFIG, and Windows
qualification by `harness/windows_port/README.md`.

## 1. Outcome and accepted decisions

- Accept a video file directly, without requiring the user to create TOML.
- Expose the full public configuration through command-line arguments.
- Keep existing profile-based commands usable and TOML optional for the new flow.
- Require `--fps` and `--cold-cap` together. If neither is supplied, use exactly
  15 FPS and cold cap 380. If only one is supplied, fail before probing or encoding.
- Do not automatically substitute the source FPS for the selected output FPS.
- Keep a table of suggested FPS/cold-cap pairs in the documentation. Suggestions
  are starting points, not universal hardware qualification claims.
- For automatic splitting, use at most 65,535 output frames per part. Select the
  minimum number of parts and distribute frames approximately equally.
- Preserve native Windows CPU/CUDA execution and the existing encoder/player path.

Implementation is not authorized to change the codec's format, physical memory
allocations, quality model, or existing source profiles merely to support this UI.

## 2. Proposed command contract

```powershell
# Direct source: 15 FPS, cold cap 380, automatic part planning.
.\tools\cavc.ps1 convert "C:\Videos\movie.mp4"

# Explicit pair and output settings.
.\tools\cavc.ps1 convert "C:\Videos\movie.mp4" `
  --fps 24 --cold-cap "2:180,3:230" `
  --width 320 --height 224 --fit pad --parts auto --device cuda

# Inspect the resolved configuration and part plan without encoding.
.\tools\cavc.ps1 convert "C:\Videos\movie.mp4" --dry-run

# Explicit output identity, part count, and optional configuration export.
.\tools\cavc.ps1 convert "C:\Videos\movie.mp4" `
  --name my-movie --parts 3 --save-config "profiles\my-movie.toml"

# Advanced public schema values, using TOML literals after the equals sign.
.\tools\cavc.ps1 convert "C:\Videos\movie.mp4" `
  --set 'video.master_denoise=false' --set 'analysis.source_canvas=[320,224]'

# Existing profile mode remains available.
.\tools\cavc.ps1 convert profiles\tears-of-steel.toml --device cuda
```

Add the direct-source form to `encode` as well. `convert` performs encoding and
disc production; `encode` ends with encoded artifacts. When multiple parts are
selected, preserve that distinction in the multipart orchestrator instead of
silently building a disc from an encode-only command. `disc` continues to consume
an existing configuration and completed artifacts. Keep the explicit `multipart`
command and its individual-part/build-only operations.

Distinguish a positional TOML profile from a source file; provide explicit
`--source` and `--config` forms for ambiguous filenames and source plus optional
configuration. Reject contradictory input forms. All advertised forms must work
through PowerShell and the Python CLI.

Direct-source mode defaults to `--parts auto`. An explicit positive count chooses
manual splitting; `--parts 1` requests a single movie and skips the preliminary
full-source count when no frame range is selected. The extracted frame count is
checked against the format limit before codec analysis, and the packer validates
it again. Existing profile-only commands retain their current workflow
selection unless the user explicitly selects automatic or manual splitting.

## 3. Configuration resolution and validation

### Shared configuration path

Refactor configuration validation into a shared operation over structured data.
The TOML loader and CLI resolver must call the same validation. Do not maintain
a second list of encoder rules or allow the CLI to bypass unknown-key checks.

Use the existing schema for public fields and their types. Provide named flags
for common fields, including raster, fit, timing, cold cap, audio filter, and
source range. Provide repeatable `--set section.key=VALUE` for the rest, including
nested tables. Parse VALUE as a TOML literal; strings require TOML quotation when
using `--set`. Named string flags accept plain argument strings.

Reject duplicate assignments to the same field across named flags and `--set`
instead of making argument order significant. Reject unknown paths and malformed
types before starting encoder work. Produce a schema coverage inventory showing
how each public TOML field can be supplied; runtime-only options such as device,
storage roots, force, region, and debug remain distinct from encode settings.

### Precedence and the paired timing rule

For ordinary fields, resolve explicit CLI settings over optional TOML values,
then source-derived values for source metadata, then shared defaults. In direct
mode, the explicitly selected input file owns `source.path`.

Treat FPS/cold cap as one assignment, not two independent fallbacks:

- Direct-source mode, including a source with an optional config: both explicit
  values override the config; neither explicit value selects 15/380. The config
  must not silently change this direct-mode default.
- Existing profile-only mode: neither explicit value preserves the profile's
  pair. Both explicit values replace it; a single explicit value is an error.
- `--set source.fps` and `--set encoder.cold_cap` count as explicit assignments
  under the same rule. They cannot bypass pairing or conflict with named flags.

Show the selected pair and its origin in the resolved plan. Keep rational FPS
strings exact. Never infer cold cap from FPS, scale it proportionally, or silently
reduce raster/cadence/cold cap after a scheduling failure.

### Direct-mode initial values

Use 320x224, `fit = "pad"`, `mosaic-gm`, no added audio gain, and the shared
resize/dither/denoise defaults. Do not copy source-specific crop expressions,
SAR repairs, endpoint snapping, or gain chains from an existing movie profile.
The suggested table is 15/380, 24 or 24000/1001 with `2:180,3:230`, and 30 or
30000/1001 with 200. Label non-default suggestions as candidates requiring
source/playback checks; other valid explicit pairs remain allowed by the schema.

## 4. Source inspection and exact frame planning

Inspect the source through FFprobe using an argument list and UTF-8 JSON. Read
the selected video stream's geometry, SAR, timing, duration, rotation metadata,
and available audio streams. Report failures with the source path and probe
diagnostics. Distinguish source metadata from selected output timing.

Before implementation changes, trace the existing FFmpeg selection/filter path
and settle these cases in code and documentation:

- Missing or invalid duration and FPS metadata; use a bounded, observable fallback
  when needed instead of inventing a duration.
- Variable frame rate, fractional FPS, and rotation; inspection, counting, and
  extraction must describe the same displayed content.
- Multiple video/audio streams; count and extract the same selected streams.
- Missing audio; produce aligned silent audio for the video duration. Preserve
  the existing mono 22.05 kHz checkpointed IMA ADPCM output.
- Audio that starts late or ends early; ensure the chosen policy keeps the
  source time relationship and pads silence where appropriate.
- Explicit duration and half-open source ranges; apply bounds in the same order
  as extraction. Account for custom filters that change frame count/timing.

Duration times target FPS is an estimate for display, not final evidence of
frame count. Share the relevant filter/range construction with extraction and
obtain the exact output count before committing part ranges. Inspect and repair
the current multipart counting path, which currently counts with only an FPS
filter rather than the full profile video-filter behavior. Reuse a matching
authenticated count/plan where possible; do not decode the same source per part
just to recount it.

FFprobe is normally short; an exact FFmpeg count may read the whole source.
Print this distinction, command/process identity, progress/log location and exit
status. `--dry-run` may perform this count but never an encoder stage or build.

## 5. Automatic multipart orchestration

For N selected output frames, resolve `parts = ceil(N / 65535)`. Reject fewer
than two frames and enforce the existing maximum of 64 parts. Fail clearly if
the selected movie cannot fit that policy; do not exceed the threshold silently.
Split into contiguous, non-overlapping, half-open ranges with no dropped or
duplicated frames and sizes differing by at most one frame.

Keep these limits distinct:

- 65,535 is the automatic part-size policy, preserving WordBuf availability.
- 83,520 remains the current single-movie format limit; do not change it.
- Explicit manual part counts use existing format checks. If a manually selected
  part exceeds 65,535 frames, show the existing WordBuf-disable consequence in
  the resolved plan rather than claiming that automatic policy was applied.

Perform multi-part execution through `tools/multipart.py`, not a second
orchestrator in `sim.py` or `parallel_run.py`. Resolve automatic count before
numbered-part validation, identity construction, locking, or completed-plan
lookup. Persist the effective count and exact ranges. Preserve part selection,
build-only mode, resume, authenticated completion, and final looping ISO behavior.
For one resolved part, use the single-movie pipeline with stable output names.
State that playback pauses while loading the next movie.

## 6. Identity, persistence, output, and cancellation

Derive a safe ASCII artifact name from the source filename, allow `--name`, and
validate Windows reserved names, length, and case-insensitive collisions. Detect
an existing output belonging to another source/config and fail before overwrite;
show how to select another name. Source paths may contain spaces and Unicode.

Freeze the effective configuration into a deterministic internal TOML because
existing stages consume profiles. It is an implementation artifact; users do
not have to create it. Keep it at a stable task-managed path under `tmp/` and
serialize it consistently. Preserve immutable configuration handoff while jobs
are live. Optional `--save-config` exports a reusable profile without overwriting
an existing file silently; its paired settings and part selection must reproduce
the resolved run. Do not store generated profiles as new public root notes.

Use source-byte identity, effective settings, and the public encoder version for
reuse. Equivalent CLI/profile settings must not trigger needless re-encoding.
Investigate the interaction of profile hashes with frozen packer settings before
changing identity rules. Do not bump the encoder version for UI-only changes;
an actual output-affecting encoder change requires a version bump.

Maintain existing resource/stem locks, output locks, artifact leases, process-tree
cancellation, CPU/CUDA selection, and storage-root propagation. Keep logs and JSON
receipts under ignored `logs/`; generated media/arrays use the selected workspace
and scratch. Cancel only task-owned processes and retain valid completed parts.

Before expensive execution, print the input, effective settings, ranges, output
paths, and available duration estimate. Clearly label an unavailable runtime
estimate rather than inventing one. `--dry-run` performs the same resolution and
validation, returns a useful exit status, and stops before encoding/assembly.

## 7. Implementation sequence

1. Trace configuration loading, FFmpeg filters/counting, artifact identity,
   launcher argument forwarding, and multipart resume. Record conclusions in
   this scoped plan as decisions become concrete.
2. Extract shared structured configuration validation and deterministic
   serialization. Preserve existing profile-only behavior.
3. Add direct-source parsing, the paired timing rule, common flags and typed
   `--set`, metadata inspection, resolved-plan output, and config export.
4. Align count/extraction semantics and handle missing audio and problematic
   metadata. Resolve the exact selected output frame count once.
5. Add automatic count selection to multipart and wire direct `convert`/`encode`
   through the existing orchestrator, including a genuine encode-only mode.
6. Complete deterministic identity, collisions, reuse, dry-run, cancellation,
   and PowerShell forwarding integration.
7. Run focused checks below, inspect failures, update documentation and examples,
   and report demonstrated support separately from untested configurations.

## 8. Validation and completion evidence

The current working tree implements the accepted CLI contract. Windows CLI,
configuration, and multipart suites pass; direct single-movie and two-part
conversions have produced independently verified ISO payloads, and the
two-part route reuses authenticated completed parts on rerun. These checks
qualify integration on this host and fixture. A separate moving-source encode
initially exposed a raw-funding trace mismatch caused by sector-rounded
prebuffer padding. The trace now accounts for those non-raw padding patterns;
the recreated 15-frame 15/380 testsrc2 source and a 48-frame 24 FPS source both
complete sim under unchanged settings. This does not establish safety for
arbitrary content or replace playback qualification.

Retain tests only for realistic externally visible failure modes:

- Pairing/default tests catch accidental source-FPS inference and half-overrides.
- CLI/profile equivalence and unknown-key/type cases catch split validation rules,
  precedence mistakes, and inability to represent public settings.
- Real short FFmpeg fixtures catch mismatched counts/ranges, missing silent audio,
  rotation/VFR mistakes, and duration/audio drift after FPS conversion.
- Synthetic frame-count boundaries at 65,534, 65,535, 65,536, 131,070 and 131,071
  catch wrong automatic part counts, gaps, overlaps, and off-by-one tails. Cover
  the 64-part limit and manual selection separately without long source encodes.
- Real subprocess argument forwarding checks catch PowerShell changes to quotes,
  filter expressions, empty strings, spaces, Unicode, and trailing backslashes.
- Existing artifact/lease/resume fixtures catch accidental re-encoding, collision
  overwrite, accepting incomplete results, and cancellation losing completed parts.

Use the pinned project Python environments. Run relevant existing Windows CLI,
configuration handoff, and multipart checks once after their code changes. Add
small tests to the existing owning harnesses, not a parallel full test framework.
Update `harness/test_audit/README.md` if ownership or entrypoints change.

Qualify the integrated CLI with one small real source at fixed selected settings:
direct-source encode-to-ISO, equivalent profile invocation reusing completed
artifacts, and explicit two-part production/resume. Retain commands, fresh logs,
exit receipts, resolved config, ranges, and independent ISO payload verification.
This proves integration, not universal cold-cap safety or real-hardware playback.

Before any substantial qualification, identify its source, settings, required
outputs, expected duration, storage roots, and completion evidence. If storage
lifetime or long-source memory behavior changes, use the actual 16,384-frame
source and all stages described in `harness/runtime_storage/README.md`. Maximum
format-index checks use synthetic fixtures. No new full-capacity source encode,
recording, burn, or publication is part of this plan by default.

Completion requires all accepted decisions implemented, existing profile usage
preserved, focused checks passing, fresh integrated evidence, and documentation
matching the final interface. Update the English and structurally matching
Japanese sections of root README/CONFIG together. Keep Windows support claims
aligned with `harness/windows_port/README.md`. Add no issue links to public docs.

## 9. Documentation ownership

- Root README: Windows setup, direct-source commands, optional profiles, output
  paths, automatic/manual splitting, loading pauses, and dry-run usage.
- CONFIG: full parameter mapping, defaults, precedence, paired timing behavior,
  `--set` literal syntax, suggestions table, and auto/manual limits.
- `harness/windows_port/README.md`: actual Windows evidence and remaining gaps.
- `harness/multipart/README.md`: new boundary/count/resume checks and entrypoints.
- This plan: implementation checklist and research conclusions, not a duplicate
  permanent configuration specification.

No new root planning document, ANALYSIS.md, or unrelated skill change is needed.
