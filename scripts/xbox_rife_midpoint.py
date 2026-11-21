"""Score the RIFE midpoint of the matched Xbox window pan fixture on CPU.

Pass the directory containing endpoint-{0,1}.png, endpoint-{0,1}-1080.png and
reference-half.png from the window quality replay. Writes RGB PNGs for FSR
evaluation and pre-display quality scores to the fixture's rife/ directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from xbox_picture_ab import ROIS, SIZE, mse, process_window, psnr, region


def read_rgb(path):
    with Image.open(path) as image:
        return np.array(image.convert('RGB'))


def image_scores(result, reference):
    """PSNR across RGB channels, within the named fixture rectangles."""
    h, w = reference.shape[:2]
    scores = {}
    for name, (x0, y0, x1, y1) in ROIS.items():
        box = (x0 * w // SIZE[0], y0 * h // SIZE[1], x1 * w // SIZE[0], y1 * h // SIZE[1])
        scores[name] = psnr([mse(region(result, box), region(reference, box))])
    return scores


def measure_pair(images, scale, output, reference):
    from framegen.interpolator import STEADY_HUD_THRESHOLD, RifeInterpolator

    height, width = reference.shape[:2]
    model = RifeInterpolator(width, height, version='4.22.lite', scale=scale,
                             device='cpu', fp16=True, cuda_graph=False)
    for slot, image in enumerate(images):
        model.load(torch.from_numpy(np.ascontiguousarray(image[:, :, ::-1])))
        model.prepare(slot)

    scores = {}
    detail = 'full' if scale == 1 else 'half'
    for steady, threshold in [('off', 0.0), ('on', STEADY_HUD_THRESHOLD)]:
        model.set_steady(threshold)
        result = model.interpolate(1, 0.5).numpy()[:, :, ::-1]
        name = f'{height}-{detail}-{steady}'
        Image.fromarray(result).save(output / f'{name}.png', compress_level=1)
        scores[name] = image_scores(result, reference)
        print(name, scores[name], flush=True)
    return scores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Checkout containing the DDA resize and RIFE implementation to load')
    parser.add_argument('--out', type=Path, help='Results directory (default: fixture/rife)')
    args = parser.parse_args()
    sys.path.insert(0, str(args.code_root.resolve()))
    from framegen.interpolator import STEADY_HUD_THRESHOLD, ensure_weights

    torch.set_num_threads(args.threads)
    output = args.out or args.fixture / 'rife'
    output.mkdir(parents=True, exist_ok=True)
    ref = read_rgb(args.fixture / 'reference-half.png')
    results = {}
    for h, w, suffix in [(1080, 1920, '-1080'), (1440, 2560, '')]:
        frames = [read_rgb(args.fixture / f'endpoint-{i}{suffix}.png') for i in (0, 1)]
        reference = process_window(ref, (w, h))
        if frames[0].shape != frames[1].shape or frames[0].shape != reference.shape:
            raise ValueError(f'expected matched {w}x{h} endpoint frames')
        results[f'{h}-hold'] = image_scores(frames[0], reference)
        for scale in (1.0, 0.5):
            results.update(measure_pair(frames, scale, output, reference))
    record = {'rife': '4.22.lite', 'model_sha256': hashlib.sha256(ensure_weights('4.22.lite').read_bytes()).hexdigest(),
              'torch': torch.__version__, 'device': 'cpu', 'inference': 'FastIFNet FP16 eager',
              'steady_threshold': STEADY_HUD_THRESHOLD,
              'measure': 'RGB PSNR (dB), pre-display output against clean midpoint at the processing resolution; one pan pair',
              'results': results}
    (output / 'rife-quality.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
