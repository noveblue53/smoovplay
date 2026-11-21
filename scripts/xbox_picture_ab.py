"""Matched SDR window-processing fixture. Requires NumPy, Pillow, ffmpeg/libx264,
PyTorch, PyOpenGL and ModernGL (Mesa EGL on headless Linux). Run from the repo:
  python scripts/xbox_picture_ab.py --work out/fixture --out out/picture
  python scripts/xbox_picture_ab.py --help
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SIZE = (2560, 1440)
LOW = (1920, 1080)
FRAMES = 8
PAN = 40  # pixels per 30 fps source frame
HUD = (80, 76, 520, 196)
FOLIAGE = (900, 780, 1340, 900)
ROIS = {"hud": HUD, "foliage": FOLIAGE, "full": (0, 0, *SIZE)}


def landscape():
    """A seeded native-resolution forest, with thin stems and leaf edges on a translating layer."""
    rng = random.Random(17422)
    w, h = SIZE
    w += PAN * FRAMES
    scene = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(scene)
    for y in range(h):
        f = y / h
        d.line((0, y, w, y), fill=(int(155 - 123 * f), int(187 - 126 * f), int(186 - 147 * f)))
    for row in range(3):
        y0 = 390 + 130 * row
        ridge = [
            (x, y0 - int(60 * math.sin(x / 156 + row) + 40 * math.sin(x / 407)))
            for x in range(-16, w + 24, 16)
        ]
        d.polygon([(0, h), *ridge, (w, h)], fill=[(106, 143, 140), (84, 124, 118), (69, 104, 89)][row])
    for _ in range(4500):
        x, y = rng.randrange(w), rng.randrange(500, h + 88)
        length = rng.randrange(18, 105)
        dx, dy = rng.randrange(-38, 40), -length
        shade = rng.randrange(-14, 24)
        colour = (50 + shade, 93 + shade, 57 + shade)
        d.line((x, y, x + dx, y + dy), fill=(75, 89, 48), width=2)
        for k in range(2, 10):
            px, py = x + dx * k // 10, y + dy * k // 10
            wing = (1 if k % 2 else -1) * (12 - k + length // 7)
            d.polygon([(px, py), (px + wing, py - 9), (px + wing // 2, py - 1)], fill=colour)
    return scene


def source(scene, index):
    x = int(index * PAN)
    img = scene.crop((x, 0, x + SIZE[0], SIZE[1]))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((86, 82, 512, 190), radius=6, fill=(29, 41, 47), outline=(155, 177, 174))
    for i, line in enumerate(
        ("CHECKPOINT  07  /  18", "RANGE   218 m      ALT   094 m", "N   15   30   NE   60   75   E")
    ):
        d.text(
            (100, 89 + 29 * i),
            line,
            font=ImageFont.load_default(size=14 + i),
            fill=(224, 237, 231),
            stroke_width=0,
        )
    for n in range(47):
        d.line((102 + 8 * n, 178, 102 + 8 * n, 181 + (4 if n % 5 == 0 else 0)), fill=(178, 202, 202))
    return img


def make_fixture(work, encoded_size):
    """Feed eight unique RGB frames to x264 once. The two policies share its decoded pixels."""
    scene = landscape()
    source(scene, 3.5).save(work / "reference-half.png")
    clip = work / "source.h264"
    # Fixed arguments split into tokens. All file paths below are passed as individual arguments.
    enc = (
        f"ffmpeg -v error -y -f rawvideo -pixel_format rgb24 -video_size {encoded_size[0]}x{encoded_size[1]} "
        "-framerate 30 -i pipe:0 -vf scale=out_color_matrix=bt709:out_range=limited,format=yuv420p "
        "-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv -c:v libx264 "
        "-threads 1 -preset veryfast -b:v 20M -maxrate 20M -bufsize 20M "
        "-x264-params bframes=0:rc-lookahead=0:scenecut=0"
    ).split() + [str(clip)]
    with subprocess.Popen(enc, stdin=subprocess.PIPE) as proc:
        for i in range(FRAMES):
            img = source(scene, i)
            img.save(work / f"clean-{i}.png")
            if img.size != encoded_size:
                img = img.resize(encoded_size, Image.Resampling.LANCZOS)
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError("x264 fixture encode failed")
    decode = ["ffmpeg", "-v", "error", "-y", "-i", str(clip)]
    decode += [
        "-vf",
        "scale=in_color_matrix=bt709:in_range=limited:out_range=full,format=rgb24",
        "-threads",
        "1",
        "-start_number",
        "0",
        str(work / "decoded-%d.png"),
    ]
    subprocess.run(decode, check=True)


def process_window(rgb, size):
    # Exercise the exact torch math in the DDA conversion on the CPU. The Windows ABI name is
    # referenced at import; no COM calls, device duplication or Windows interfaces run here.
    if os.name == "nt":
        from framegen.dda import to_bgr
    else:
        with patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True):
            from framegen.dda import to_bgr
    import torch

    bgra = np.empty((*rgb.shape[:2], 4), dtype=np.uint8)
    bgra[..., :3], bgra[..., 3] = rgb[..., ::-1], 255
    bgr = to_bgr(torch.from_numpy(bgra), *size).numpy()
    return np.ascontiguousarray(bgr[..., ::-1])


def endpoints(work):
    for j in (0, 1):
        img = Image.open(work / f"decoded-{3 + j}.png").convert("RGB")
        img = img.resize(SIZE, Image.Resampling.BILINEAR) if img.size != SIZE else img
        img.save(work / f"endpoint-{j}.png")
        Image.fromarray(process_window(np.asarray(img), LOW)).save(work / f"endpoint-{j}-1080.png")


class Display:
    """Use the application's EASU and RCAS programs at their production uniforms."""

    def __init__(self):
        import moderngl

        if sys.platform == "linux":
            os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
            self.ctx = moderngl.create_standalone_context(require=450, backend="egl")
        else:
            self.ctx = moderngl.create_standalone_context(require=450)
        from OpenGL import GL

        from framegen.fsr import FsrScaler

        self.fsr = FsrScaler(GL, sharpness=0.5)
        self.output = self.ctx.simple_framebuffer(SIZE, components=4, dtype="f1")

    def render(self, img):
        w, h = img.shape[1], img.shape[0]
        if (w, h) == SIZE:
            return img  # display's 1:1 blit; no look, fill or upscale
        tex = self.ctx.texture((w, h), 3, img.tobytes(), alignment=1)
        tex.repeat_x = tex.repeat_y = False
        try:
            self.fsr.draw(tex.glo, w, h, self.output.glo, 0, 0, *SIZE)
            data = self.output.read(components=3, alignment=1)
            return np.frombuffer(data, np.uint8).reshape(SIZE[1], SIZE[0], 3)[::-1].copy()
        finally:
            tex.release()

    def close(self):
        self.fsr.close()
        self.output.release()
        self.ctx.release()


def region(img, box):
    x, y, r, b = box
    return img[y:b, x:r]


def mse(a, b):
    error = a.astype(np.float32) - b
    return float(np.square(error).mean(dtype=np.float64))


def psnr(errors):
    return round(10 * math.log10(255**2 / np.mean(errors)), 3) if np.any(errors) else "exact"


def sample_sheet(
    reference,
    low,
    native,
    title,
    labels=("Clean generated reference", "1080p processing + FSR 1", "1440p processing, 1:1"),
):
    margin, width, height = 16, 440, 120
    sheet = Image.new("RGB", (3 * width + 4 * margin, 2 * height + 112), (28, 34, 41))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    draw.text((margin, 9), title, fill="white", font=font)
    for k, (label, im) in enumerate(zip(labels, (reference, low, native))):
        x = margin + k * (width + margin)
        draw.text((x, 38), label, fill=(180, 203, 215), font=font)
        for j, box in enumerate((HUD, FOLIAGE)):
            sheet.paste(Image.fromarray(region(im, box)), (x, 63 + j * (height + 26)))
    return sheet


def measure(work, out, encoded_size, mids):
    disp = Display()
    errors = {s: {r: [] for r in ROIS} for s in ("1080p", "native")}
    if encoded_size == LOW:
        errors["1080p_client_one_upscale"] = {r: [] for r in ROIS}
    temporal = {s: [] for s in errors}
    previous = {}
    hashes = {s: hashlib.sha256() for s in errors}
    pan_samples = []
    clip = work / "source.h264"
    data = {
        "fixture_sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
        "encoded_fixture_mbps": round(8 * clip.stat().st_size / (FRAMES / 30) / 1e6, 3),
        "stream": "SDR H.264 4:2:0, eight 30 fps source frames",
        "encoded": encoded_size,
        "output": SIZE,
        "client_for_1080p_and_native_policies": SIZE,
        "processing": {"1080p": LOW, "native": SIZE},
        "physical_monitor_hz": None,
        "stock_window_received_video_mbps": None,
        "capture_model": "decoded RGB supplied to DDA conversion on CPU",
        "client_scaling": "1:1" if encoded_size == SIZE else "simulated app bilinear upscale to 1440p",
        "gl_renderer": disp.ctx.info["GL_RENDERER"],
        "rois_xyxy": ROIS,
    }
    try:
        for i in range(FRAMES):
            decoded = Image.open(work / f"decoded-{i}.png").convert("RGB")
            if decoded.size != encoded_size:
                raise ValueError(
                    f"Fixture is {decoded.size}, expected {encoded_size}. Check --encoded-size/--work."
                )
            client = decoded if decoded.size == SIZE else decoded.resize(SIZE, Image.Resampling.BILINEAR)
            rgb = np.asarray(client)
            clean = np.asarray(Image.open(work / f"clean-{i}.png").convert("RGB"))
            images = {}
            routes = [("1080p", rgb, LOW), ("native", rgb, SIZE)]
            if encoded_size == LOW:
                # 1080p decoded into a 1080p client, shown fullscreen on a 1440p monitor.
                # Both policies have identical processing geometry here and only FSR scales.
                routes.append(("1080p_client_one_upscale", np.asarray(decoded), LOW))
            for mode, pixels, size in routes:
                result = images[mode] = disp.render(process_window(pixels, size))
                hashes[mode].update(result.tobytes())
                for name, box in ROIS.items():
                    errors[mode][name].append(mse(region(result, box), region(clean, box)))
                if i:
                    diff = (
                        result.astype(np.int16)
                        - previous[mode]
                        - (clean.astype(np.int16) - previous["clean"])
                    )
                    temporal[mode].append(float(np.abs(diff).mean(dtype=np.float64)))
                previous[mode] = result
            previous["clean"] = clean
            sheet = sample_sheet(
                clean,
                images["1080p"],
                images["native"],
                f"Source frame {i}  /  fixed HUD above, panning foliage below  /  1:1 crops",
            )
            pan_samples.append(sheet)
            if i == 3:
                sheet.save(out / "real-frame.png")
                if encoded_size == LOW:
                    sample_sheet(
                        images["1080p_client_one_upscale"],
                        images["1080p"],
                        images["native"],
                        "1080p encoded stream  /  one decoded source frame  /  1:1 crops at 1440p output",
                        labels=(
                            "1080p client, FSR 1",
                            "1440p client, 1080p processing + FSR 1",
                            "1440p client, 1440p processing",
                        ),
                    ).save(out / "one-upscale.png")
        # Three times slower than gameplay so consecutive source images can be inspected by eye.
        pan_samples[0].save(
            out / "pan.webp",
            save_all=True,
            append_images=pan_samples[1:],
            duration=100,
            loop=0,
            lossless=True,
            method=1,
        )
        data["clean_reference_psnr_db"] = {
            s: {r: psnr(e) for r, e in groups.items()} for s, groups in errors.items()
        }
        data["frame_difference_mae_rgb_0_255"] = {
            s: round(float(np.mean(e)), 3) for s, e in temporal.items()
        }
        data["output_sequence_sha256"] = {s: h.hexdigest() for s, h in hashes.items()}
        if mids:
            ref = np.asarray(Image.open(work / "reference-half.png").convert("RGB"))
            midpoint_images = [Image.open(p).convert("RGB") for p in mids]
            if [im.size for im in midpoint_images] != [LOW, SIZE]:
                raise ValueError("--midpoints needs 1920x1080 then 2560x1440 RGB images")
            low, native = [disp.render(np.asarray(im)) for im in midpoint_images]
            sample_sheet(
                ref,
                low,
                native,
                "RIFE 4.22 Lite midpoint  /  fixed HUD above, panning foliage below  /  1:1 crops",
            ).save(out / "rife-midpoint.png")
            data["midpoint_psnr_db"] = {
                s: {r: psnr([mse(region(im, b), region(ref, b))]) for r, b in ROIS.items()}
                for s, im in (("1080p", low), ("native", native))
            }
    finally:
        disp.close()
    (out / "picture.json").write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--encoded-size", choices=("1080p", "1440p"), default="1440p")
    ap.add_argument("--work", type=Path, required=True, help="Fixture/decoded-frame directory")
    ap.add_argument("--out", type=Path, required=True, help="Results directory")
    ap.add_argument(
        "--code-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Checkout from which to import the actual DDA resize and FSR shaders",
    )
    ap.add_argument(
        "--reuse",
        action="store_true",
        help="Read the exact encoded and decoded fixture already in --work",
    )
    ap.add_argument(
        "--prepare-only", action="store_true", help="Generate the fixture and matched RIFE input pairs"
    )
    ap.add_argument(
        "--midpoints",
        type=Path,
        nargs=2,
        metavar=("RIFE_1080", "RIFE_1440"),
        help="Optional full-size RGB midpoint PNGs for decoded frames 3 and 4",
    )
    args = ap.parse_args()
    sys.path.insert(0, str(args.code_root.resolve()))
    import torch

    torch.set_num_threads(2)
    args.work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    encoded_size = LOW if args.encoded_size == "1080p" else SIZE
    if not args.reuse:
        make_fixture(args.work, encoded_size)
    endpoints(args.work)
    if not args.prepare_only:
        measure(args.work, args.out, encoded_size, args.midpoints)


if __name__ == "__main__":
    main()
