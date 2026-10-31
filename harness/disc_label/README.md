# Configurable ISO volume label: implementation plan

## Goal and current behavior

Allow users to set the ISO volume label that a computer can display after
mounting the image or reading the recorded CD. This is a filesystem volume
identifier, separate from the Sega CD boot header's title and output filenames.
This document describes planned work; the option is not implemented yet.

`tools/iso9660.py` already accepts a volume identifier and writes it into the
primary volume descriptor. `tools/build_disc.py` currently supplies
`SCFMV_DLT`; `tools/multipart_build.py` supplies `CAVC_MULTIPART`.

## User interface and validation

Add `release.disc_label` to the profile schema and `--disc-label` to CLI modes
that build a disc, including `convert`, `disc`, and multipart assembly paths.
Preserve existing configuration precedence: an explicit named CLI option
overrides the profile. Handle conflicting named options and `--set` through
the existing conflict rules. Support profile export and show the effective
label in mode help and the resolved build plan.

```powershell
tools\cavc.ps1 convert "E:\VIDEO\movie.mp4" --disc-label "HORIZON_CHASE_TURBO"
```

```toml
[release]
disc_label = "HORIZON_CHASE_TURBO"
```

The agreed label contract is:

- Require a string of 1–32 characters.
- Accept only ASCII Latin letters, ASCII digits, and underscore: `[A-Za-z0-9_]`.
- Normalize accepted letters to uppercase before displaying, storing, comparing,
  or writing the effective label.
- Reject empty strings, spaces, punctuation other than underscore, control
  characters, non-ASCII text, and values longer than 32 characters.
- Do not silently trim, truncate, replace characters, or transliterate input.
- Preserve existing defaults when the setting is omitted: `SCFMV_DLT` for a
  single movie and `CAVC_MULTIPART` for a multipart disc.

The label does not change `--name`, artifact stems, ISO filenames, or the Sega
CD boot title. Unicode labels and Joliet support are outside this change.

## Implementation

1. Extend `tools/encode_config.py` with the optional metadata field and a
   shared validation/normalization path. Audit configuration exports and
   schema-version requirements. Profiles without the new key keep working.
2. Extend `tools/cavc_cli.py` argument parsing, precedence, help, `--set`
   reference, generated profiles, saved profiles, and dry-run output. Ensure
   `tools/cavc.ps1` forwards the option through its existing argument path.
3. Resolve the label once per disc build and propagate it through direct
   builds, profile builds, multipart planning/assembly, DEBUG/release builds,
   and supported regions. All parts share the final disc's label.
4. Replace the fixed arguments in `tools/build_disc.py` and
   `tools/multipart_build.py` with the resolved label. Enforce the same contract
   in the low-level ISO writer so direct `tools/iso9660.py --volume` calls
   cannot bypass validation.
5. Record the normalized label in disc build metadata and multipart manifests.
   Audit reuse checks so changing a label invalidates the assembled ISO while
   preserving matching encoded video, packed streams, and player artifacts.
   Do not include disc-only metadata in encode identity or force a re-encode.
6. Extend independent ISO verification to read the primary volume descriptor
   and compare its 32-byte, space-padded volume identifier with the expected
   normalized label. Preserve atomic replacement of verified output images.

Changing the label occupies the existing fixed-size descriptor field. It
requires no new sectors, stream-layout changes, audio/video budget changes,
or player decoding changes. Rebuilding an existing disc must reuse matching
upstream artifacts under the existing locks and leases.

## Validation and completion evidence

Follow `harness/test_audit/README.md` when choosing checks. Cover concrete
failure modes rather than duplicating implementation details:

- Reject invalid characters and lengths; accept the 1- and 32-character bounds
  and normalize lowercase input consistently across CLI and profile paths.
- Verify CLI precedence and conflicting overrides so a requested label is not
  silently ignored or changed during profile export.
- Read the label independently from finished single-movie and multipart ISOs,
  including default behavior and representative region/build-mode paths.
- Rebuild with a changed label from matching existing artifacts and establish
  that encoding did not run. Check that ISO metadata and manifests reflect
  the new label and that embedded stream files remain unchanged.
- Confirm the volume name by mounting a generated image on the supported
  Windows host. Burning a CD is a separate, explicitly requested qualification;
  preparing or implementing this option does not authorize a burn.

Use small fixtures and existing completed artifacts where possible. Store
diagnostic TSV/JSON in ignored `logs/`. Report verified ISO contents separately
from host display behavior; do not claim recorded-disc qualification without
an actual recorded-disc check.

## Documentation and delivery

Update `README.md` with CLI/profile examples and `CONFIG.md` with precedence,
defaults, the exact character contract, and disc-only rebuild behavior. Public
root Markdown must keep matching English and Japanese sections. Keep mode
help defaults and allowed values beside the relevant parameter. Update the
Windows qualification record only for checks actually completed.

Completion requires the label to survive configuration resolution, image
assembly, independent ISO verification, and disc metadata recording in both
single-movie and multipart workflows. Use Japanese commit messages and the
repository owner's author/committer identity.
