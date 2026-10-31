#!/usr/bin/env python3
"""Compare saved decisions, numeric traces and media sample data of completed sims."""

import argparse
import csv
import hashlib
from pathlib import Path
import pickle
import sys
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))


def equal(left, right, where):
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        a, b = np.asarray(left), np.asarray(right)
        if a.shape != b.shape or a.dtype != b.dtype:
            raise AssertionError(f'{where}: shape/dtype differ: {a.shape}/{a.dtype}, {b.shape}/{b.dtype}')
        np.testing.assert_array_equal(a, b, err_msg=where)
    elif isinstance(left, dict):
        if left.keys() != right.keys():
            raise AssertionError(f'{where}: keys differ')
        for key in left:
            equal(left[key], right[key], f'{where}.{key}')
    elif isinstance(left, (tuple, list)):
        if len(left) != len(right):
            raise AssertionError(f'{where}: lengths differ')
        for index, (a, b) in enumerate(zip(left, right)):
            equal(a, b, f'{where}[{index}]')
    elif isinstance(left, float) and np.isnan(left):
        if not np.isnan(right):
            raise AssertionError(f'{where}: NaN differs')
    elif left != right:
        raise AssertionError(f'{where}: {left!r} != {right!r}')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def compare_wav_samples(left_path, right_path):
    """Compare the audio format and decoded PCM, ignoring RIFF metadata chunks."""
    with wave.open(str(left_path), 'rb') as left:
        left_params = left.getparams()
        left_samples = left.readframes(left_params.nframes)
    with wave.open(str(right_path), 'rb') as right:
        right_params = right.getparams()
        right_samples = right.readframes(right_params.nframes)
    if left_params != right_params:
        raise AssertionError(
            f'WAV format differs: {left_path} {left_params}, '
            f'{right_path} {right_params}')
    if left_samples != right_samples:
        offset = next((i for i, (a, b) in enumerate(zip(left_samples, right_samples))
                       if a != b), min(len(left_samples), len(right_samples)))
        raise AssertionError(
            f'WAV PCM differs at byte {offset} '
            f'(sample {offset // left_params.sampwidth}): '
            f'{left_path} vs {right_path}')
    return left_params


def normalize_path_provenance(decisions):
    """Normalize only checkout/profile paths, not effective simulation settings."""
    config = decisions.get('config')
    if not isinstance(config, dict):
        raise AssertionError('decisions.config is missing or invalid')
    profile = config.get('profile')
    source = config.get('source')
    if not isinstance(profile, dict) or not isinstance(source, dict):
        raise AssertionError('decisions.config profile/source provenance is missing')
    for key in ('path', 'sha256'):
        if key not in profile:
            raise AssertionError(f'decisions.config.profile.{key} is missing')
        profile[key] = f'<normalized profile {key}>'
    if 'path' not in source:
        raise AssertionError('decisions.config.source.path is missing')
    source['path'] = '<normalized source path>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--tsv', type=Path, required=True)
    parser.add_argument(
        '--normalize-path-provenance', action='store_true',
        help='ignore profile file/hash and source path fields; verify source bytes and settings separately')
    args = parser.parse_args()
    rows = []
    # Only local, trusted encoder outputs may be unpickled.
    with (args.baseline / 'decisions.pkl').open('rb') as f:
        left = pickle.load(f)
    with (args.candidate / 'decisions.pkl').open('rb') as f:
        right = pickle.load(f)
    if args.normalize_path_provenance:
        normalize_path_provenance(left)
        normalize_path_provenance(right)
        rows.append(['provenance', 'profile path/hash and source path', 'NORMALIZED'])
    equal(left, right, 'decisions')
    rows.append(['decisions.pkl', 'all fields/frames', 'IDENTICAL'])
    for name in ('stats.npz', 'buffer_remaining.npz', 'seg_palettes.npz'):
        with np.load(args.baseline / name, allow_pickle=False) as a, np.load(args.candidate / name, allow_pickle=False) as b:
            if set(a.files) != set(b.files):
                raise AssertionError(f'{name}: fields differ')
            for key in a.files:
                equal(a[key], b[key], f'{name}.{key}')
        rows.append([name, 'all numeric fields', 'IDENTICAL'])
    for name in ('miss_masks.npy', 'palettes.bin'):
        a, b = digest(args.baseline / name), digest(args.candidate / name)
        if a != b:
            raise AssertionError(f'{name}: bytes differ')
        rows.append([name, a, 'IDENTICAL'])
    for name in ('audio_22k05_s16_mono.wav', 'audio_playback_adpcm22_rf5c.wav'):
        params = compare_wav_samples(args.baseline / name, args.candidate / name)
        description = (f'{params.nchannels}ch {params.sampwidth * 8}-bit '
                       f'{params.framerate}Hz {params.nframes} frames')
        rows.append([name, description, 'IDENTICAL PCM'])
    args.tsv.parent.mkdir(parents=True, exist_ok=True)
    with args.tsv.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['artifact', 'comparison', 'result'])
        writer.writerows(rows)
    print(f'PASS: {len(left["frames"])} frames, all saved decisions/traces and audio; {args.tsv}')


if __name__ == '__main__':
    main()
