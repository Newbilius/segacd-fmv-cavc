"""Observable direct-source CLI checks using a real short FFmpeg source."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools" / "cavc_cli.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(ROOT / "tools"))
import cavc_cli
from cavc_cli import claim_output_identity
import encode_config
import multipart
import source_inspection


@unittest.skipUnless(shutil.which("ffmpeg") and PYTHON.is_file(),
                     "requires pinned Python and FFmpeg")
class DirectCliTests(unittest.TestCase):
    def test_invalid_duration_metadata_falls_back_to_exact_stream_time_base(self):
        info = {"metadata": {"format": {"duration": "N/A"}},
                "video": {"duration": "N/A", "duration_ts": "90000",
                          "time_base": "1/90000"}}
        self.assertEqual(source_inspection.source_duration(info), "1.0")
        self.assertIsNone(source_inspection.source_duration(
            {"metadata": {}, "video": {"duration": "N/A"}}))

    def test_named_identity_reserves_name_and_rejects_other_settings_or_orphans(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "movie.identity.json"
            output = root / "movie.iso"
            first = {"schema": 1, "name": "movie", "config_digest": "same"}
            claim_output_identity(identity, first, [output])
            claim_output_identity(identity, first, [output])
            with self.assertRaisesRegex(ValueError, "another source/config"):
                claim_output_identity(identity,
                                      {**first, "config_digest": "different"},
                                      [output])
            orphan_identity = root / "orphan.identity.json"
            orphan = root / "orphan.iso"
            orphan.write_bytes(b"existing artifact")
            with self.assertRaisesRegex(ValueError, "without a matching identity"):
                claim_output_identity(orphan_identity,
                                      {**first, "name": "orphan"}, [orphan])

    def test_named_identity_detects_release_and_multipart_orphans_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "out"
            output_root.mkdir()
            release_iso = output_root / "Movie_release.iso"
            release_iso.write_bytes(b"stale release")
            with mock.patch.object(cavc_cli, "ROOT", root):
                paths = cavc_cli.named_output_paths("movie")
            self.assertIn(release_iso, paths)
            with self.assertRaisesRegex(ValueError, "without a matching identity"):
                claim_output_identity(root / "movie.identity.json",
                                      {"schema": 1, "name": "movie",
                                       "config_digest": "new"}, paths)
            release_iso.unlink()
            multipart_iso = output_root / "Movie-multipart2-abcd_us_release.iso"
            multipart_iso.write_bytes(b"stale multipart")
            with mock.patch.object(cavc_cli, "ROOT", root):
                paths = cavc_cli.named_output_paths("movie")
            self.assertIn(multipart_iso, paths)

    def test_output_names_are_windows_safe_ascii_stems(self):
        self.assertEqual(cavc_cli.safe_artifact_name(None, Path("movie 日本.mp4")),
                         "movie")
        for name in ("CON", "prn", "CON.toml", "LPT1.iso", "has spaces",
                     "-starts-with-punctuation", "trailing.", "", "x" * 181):
            with self.subTest(name=name), self.assertRaises(ValueError):
                cavc_cli.safe_artifact_name(name, Path("source.mp4"))

    def test_source_config_set_keeps_named_frozen_profile_for_all_stages(self):
        with tempfile.TemporaryDirectory(prefix="cavc-named-config-") as temporary:
            root = Path(temporary)
            source = root / "input with spaces.mp4"
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=320x224:r=15:d=1",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True, cwd=ROOT)
            base = encode_config.load_profile(ROOT / "profiles/sonic-jam-op.toml")
            profile_data = {"schema_version": encode_config.SCHEMA_VERSION,
                            **base.data}
            profile_data["source"].update(path=str(source), duration="1")
            config = root / "base.toml"
            config.write_text(multipart.toml_text(profile_data), encoding="utf-8")
            stages = []

            def record_stage(name, command, *, env):
                stages.append((name, command))
                return {"exit_code": 0}

            with mock.patch.object(cavc_cli, "ROOT", root), \
                    mock.patch.object(cavc_cli, "run_stage", side_effect=record_stage):
                result = cavc_cli.main([
                    "convert", "--source", str(source), "--config", str(config),
                    "--name", "named-config-run", "--fps", "30", "--cold-cap", "200",
                    "--set", 'video.output_dither="none"', "--device", "cpu"])

            expected_profile = root / "tmp/cli_profiles/named-config-run.toml"
            self.assertEqual(result, 0)
            self.assertTrue(expected_profile.is_file())
            resolved = encode_config.load_profile(expected_profile)
            self.assertEqual(resolved.data["source"]["path"], str(source))
            self.assertEqual(resolved.data["video"]["output_dither"], "none")
            self.assertEqual([name for name, _command in stages], ["encode", "disc"])
            self.assertTrue(all(str(expected_profile) in command
                                for _name, command in stages))

    def test_timing_half_pair_fails_before_source_probe(self):
        result = subprocess.run(
            [str(PYTHON), str(CLI), "encode", "missing-source.mp4", "--fps", "24"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--fps and --cold-cap must be supplied together", result.stderr)
        self.assertNotIn("FFprobe", result.stderr)

    def test_short_source_exact_count_and_schema_validation(self):
        with tempfile.TemporaryDirectory(prefix="cavc-direct-") as temporary:
            source = Path(temporary) / "短い source.mp4"
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=320x224:r=15:d=1",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True, cwd=ROOT)
            result = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("FPS: 15", result.stdout)
            self.assertIn("Cold cap: 380", result.stdout)
            self.assertIn("Duration: 1.000000 s (FFprobe)", result.stdout)
            self.assertIn("Selected video stream: 0:v:0", result.stdout)
            self.assertIn("audio available indexes=none; aligned silent audio",
                          result.stdout)
            self.assertIn("Exact output frames: 15", result.stdout)
            self.assertIn("Encoder stages: not started", result.stdout)
            external_cwd = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run"],
                cwd=temporary, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(external_cwd.returncode, 0, external_cwd.stderr)
            self.assertIn(str(ROOT / "logs"), external_cwd.stdout)
            self.assertFalse((Path(temporary) / "logs").exists())
            invalid_parts = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--parts", "64"], cwd=ROOT, capture_output=True, text=True,
                encoding="utf-8")
            self.assertNotEqual(invalid_parts.returncode, 0)
            self.assertIn("each part needs at least two output frames",
                          invalid_parts.stderr)
            self.assertNotIn("Hashing source bytes", invalid_parts.stdout)
            empty_parts = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--parts", ""],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(empty_parts.returncode, 0)
            self.assertIn("--parts must be 'auto'", empty_parts.stderr)
            self.assertNotIn("FFprobe", empty_parts.stderr)
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                forwarded = subprocess.run(
                    [powershell, "-NoProfile", "-File", str(ROOT / "tools/cavc.ps1"),
                     "encode", str(source), "--dry-run"], cwd=ROOT,
                    capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(forwarded.returncode, 0, forwarded.stderr)
                self.assertIn("Exact output frames: 15", forwarded.stdout)

            resolved_set = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--set", 'source.fps="30"', "--set", "encoder.cold_cap=200",
                 "--set", "source.start_frame=3", "--set", "source.end_frame=10"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(resolved_set.returncode, 0, resolved_set.stderr)
            self.assertIn("FPS: 30", resolved_set.stdout)
            self.assertIn("Cold cap: 200", resolved_set.stdout)
            self.assertIn("Exact output frames: 7", resolved_set.stdout)

            profile_data = {
                "schema_version": encode_config.SCHEMA_VERSION,
                **encode_config.load_profile(ROOT / "profiles/sonic-jam-op.toml").data,
            }
            profile_data["source"].update(path=str(source), duration="1")
            profile_path = Path(temporary) / "base.toml"
            profile_path.write_text(multipart.toml_text(profile_data), encoding="utf-8")
            source_config_plan = subprocess.run(
                [str(PYTHON), str(CLI), "encode", "--source", str(source),
                 "--config", str(profile_path), "--name", "source-config-smoke",
                 "--dry-run"], cwd=ROOT, capture_output=True, text=True,
                encoding="utf-8")
            self.assertEqual(source_config_plan.returncode, 0, source_config_plan.stderr)
            self.assertIn("out\\source-config-smoke", source_config_plan.stdout)
            profile_plan = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(profile_path), "--parts", "auto",
                 "--dry-run"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(profile_plan.returncode, 0, profile_plan.stderr)
            self.assertIn("Exact output frames: 30", profile_plan.stdout)
            self.assertIn("Resolved parts: 1", profile_plan.stdout)
            part_config_path = Path(temporary) / "two-parts.toml"
            part_config = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(profile_path), "--parts", "2",
                 "--dry-run", "--save-config", str(part_config_path)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(part_config.returncode, 0, part_config.stderr)
            self.assertEqual(encode_config.load_profile(part_config_path)
                             .data["multipart"]["parts"], 2)
            reproduced = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(part_config_path), "--dry-run"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(reproduced.returncode, 0, reproduced.stderr)
            self.assertIn("Exact output frames: 30", reproduced.stdout)
            self.assertIn("Resolved parts: 2", reproduced.stdout)

            export_path = Path(temporary) / "resolved.toml"
            exported = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--save-config", str(export_path)], cwd=ROOT, capture_output=True,
                text=True, encoding="utf-8")
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertTrue(export_path.is_file())
            load_export = subprocess.run(
                [str(PYTHON), str(ROOT / "tools" / "encode_config.py"), str(export_path)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(load_export.returncode, 0, load_export.stderr)
            exported_profile = encode_config.load_profile(export_path)
            self.assertEqual(exported_profile.data["source"]["fps"], "15")
            self.assertEqual(exported_profile.data["encoder"]["cold_cap"], 380)
            reproduce_direct_export = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(export_path), "--dry-run"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(reproduce_direct_export.returncode, 0,
                             reproduce_direct_export.stderr)
            self.assertIn("FPS: 15", reproduce_direct_export.stdout)
            self.assertIn("Cold cap: 380", reproduce_direct_export.stdout)
            self.assertIn("Exact output frames: not counted", reproduce_direct_export.stdout)
            direct_parts_export = Path(temporary) / "direct-two-parts.toml"
            exported_parts = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--parts", "2",
                 "--dry-run", "--save-config", str(direct_parts_export)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(exported_parts.returncode, 0, exported_parts.stderr)
            self.assertEqual(encode_config.load_profile(direct_parts_export)
                             .data["multipart"]["parts"], 2)
            reproduce_direct_parts = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(direct_parts_export), "--dry-run"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(reproduce_direct_parts.returncode, 0,
                             reproduce_direct_parts.stderr)
            self.assertIn("Resolved parts: 2", reproduce_direct_parts.stdout)
            self.assertIn("Exact output frames: 15", reproduce_direct_parts.stdout)
            refused = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--save-config", str(export_path)], cwd=ROOT, capture_output=True,
                text=True, encoding="utf-8")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to overwrite", refused.stderr)

            invalid = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--set", "video.unknown_cli_test=1"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("unknown [video] keys", invalid.stderr)
            invalid_type = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--set", "video.master_denoise=1"], cwd=ROOT,
                capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(invalid_type.returncode, 0)
            self.assertIn("video.master_denoise must be bool", invalid_type.stderr)
            self.assertNotIn("FFmpeg exact count", invalid_type.stdout)
            invalid_duration = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                 "--duration", "not-a-duration"], cwd=ROOT,
                capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(invalid_duration.returncode, 0)
            self.assertIn("source.duration must be positive seconds", invalid_duration.stderr)
            self.assertNotIn("FFmpeg exact count", invalid_duration.stdout)

    def test_vfr_count_matches_timestamp_filtered_extraction(self):
        with tempfile.TemporaryDirectory(prefix="cavc-vfr-") as temporary:
            source = Path(temporary) / "vfr.mp4"
            extracted = Path(temporary) / "frame-%03d.png"
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x224:rate=15:duration=2",
                "-vf", r"settb=1/1000,setpts=N*1000/15+if(gte(N\,10)\,250\,0)",
                "-fps_mode", "vfr", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(source)], check=True, cwd=ROOT)
            plan = subprocess.run(
                [str(PYTHON), str(CLI), "encode", str(source), "--dry-run"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(plan.returncode, 0, plan.stderr)
            count_line = next(line for line in plan.stdout.splitlines()
                              if line.strip().startswith("Exact output frames: "))
            planned = int(count_line.rsplit(" ", 1)[1])
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-vf", "setsar=1,"
                "scale=640:448:flags=lanczos,hqdn3d=6:6:8:8,gblur=sigma=1.6,"
                "scale=320:224:flags=lanczos,pad=320:224:(ow-iw)/2:(oh-ih)/2:color=black,"
                "fps=15", str(extracted)], check=True, cwd=ROOT)
            self.assertEqual(len(list(Path(temporary).glob("frame-*.png"))), planned)

    def test_count_and_extraction_select_the_same_first_video_stream(self):
        with tempfile.TemporaryDirectory(prefix="cavc-streams-") as temporary:
            source = Path(temporary) / "multiple-video-streams.mkv"
            extracted = Path(temporary) / "selected-%03d.png"
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=320x224:r=15:d=1",
                "-f", "lavfi", "-i", "color=c=red:s=640x480:r=30:d=2",
                "-map", "0:v:0", "-map", "1:v:0", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(source)], check=True, cwd=ROOT)
            info = source_inspection.probe(source)
            self.assertEqual(info["video"].get("index"), 0)
            planned = source_inspection.count_output_frames(
                source, "15", duration="2", video_filter="null")
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-t", "2", "-i", str(source), "-map", "0:v:0",
                "-vf", "fps=15", str(extracted)], check=True, cwd=ROOT)
            self.assertEqual(planned, 15)
            self.assertEqual(len(list(Path(temporary).glob("selected-*.png"))), planned)

    def test_rotation_fractional_output_clock_and_extraction_agree(self):
        with tempfile.TemporaryDirectory(prefix="cavc-rotated-") as temporary:
            base = Path(temporary) / "base.mp4"
            source = Path(temporary) / "rotated.mp4"
            extracted = Path(temporary) / "frame-%03d.png"
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x224:rate=24:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base)],
                check=True, cwd=ROOT)
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-display_rotation:v:0", "90", "-i", str(base), "-c", "copy",
                str(source)], check=True, cwd=ROOT)
            info = source_inspection.probe(source)
            self.assertEqual(info["rotation"], 90)
            self.assertEqual((info["display_width"], info["display_height"]), (224, 320))
            profile = subprocess.run([
                str(PYTHON), str(CLI), "encode", str(source), "--dry-run",
                "--fps", "24000/1001", "--cold-cap", "200"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(profile.returncode, 0, profile.stderr)
            self.assertIn("display=224x320", profile.stdout)
            self.assertIn("rotation=90.0 degrees", profile.stdout)
            line = next(line for line in profile.stdout.splitlines()
                        if line.strip().startswith("Exact output frames: "))
            planned = int(line.rsplit(" ", 1)[1])
            from video_geometry import raw_filter, source_filter
            master = source_filter(320, 224, info["display_width"],
                                   info["display_height"], denoise=True)
            raw = raw_filter(320, 224, info["display_width"],
                             info["display_height"])
            self.assertEqual(source_inspection.count_output_frames(
                source, "24000/1001", duration="1", video_filter=master), planned)
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-t", "1", "-i", str(source), "-map", "0:v:0",
                "-vf", f"{raw},fps=24000/1001", str(extracted)],
                check=True, cwd=ROOT)
            self.assertEqual(len(list(Path(temporary).glob("frame-*.png"))), planned)

    def test_late_start_and_early_end_audio_are_padded_on_video_clock(self):
        with tempfile.TemporaryDirectory(prefix="cavc-audio-offset-") as temporary:
            source = Path(temporary) / "offset.mkv"
            extracted = Path(temporary) / "aligned.wav"
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x224:r=15:d=2",
                "-itsoffset", "0.5", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=22050:duration=0.6",
                "-f", "lavfi", "-i",
                "sine=frequency=880:sample_rate=22050:duration=2",
                "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
                "-c:v", "libx264",
                "-c:a", "pcm_s16le", "-pix_fmt", "yuv420p", str(source)],
                check=True, cwd=ROOT)
            info = source_inspection.probe(source)
            self.assertEqual([stream["index"] for stream in info["audio"]], [1, 2])
            subprocess.run([
                shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-t", "2", "-i", str(source), "-map", "0:a:0", "-vn",
                "-af", source_inspection.aligned_audio_filter("", "2"),
                "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(extracted)],
                check=True, cwd=ROOT)
            with wave.open(str(extracted), "rb") as audio:
                self.assertEqual(audio.getframerate(), 22050)
                samples = np.frombuffer(audio.readframes(audio.getnframes()), "<i2")
            self.assertEqual(samples.size, 2 * 22050)
            self.assertEqual(int(np.max(np.abs(samples[:int(.4 * 22050)]))), 0)
            self.assertGreater(int(np.max(np.abs(samples[int(.6 * 22050):int(1.0 * 22050)]))), 1000)
            self.assertEqual(int(np.max(np.abs(samples[int(1.3 * 22050):]))), 0)


if __name__ == "__main__":
    unittest.main()
