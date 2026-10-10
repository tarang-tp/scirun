"""Render the Mandelbrot set to a PNG."""

import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image


def mandelbrot(width: int, height: int, max_iter: int) -> np.ndarray:
    """Return an array of escape-iteration counts, shape (height, width)."""
    x = np.linspace(-2.0, 1.0, width)
    y = np.linspace(-1.2, 1.2, height)
    c = x[np.newaxis, :] + 1j * y[:, np.newaxis]
    z = np.zeros_like(c)
    counts = np.zeros(c.shape, dtype=np.int32)
    for i in range(max_iter):
        active = np.abs(z) <= 2
        z[active] = z[active] ** 2 + c[active]
        counts[active] = i
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--out", type=Path, default=Path("results/mandelbrot.png"))
    args = parser.parse_args()

    print(f"Rendering {args.width}x{args.height}, max_iter={args.max_iter}", flush=True)
    start = time.perf_counter()
    counts = mandelbrot(args.width, args.height, args.max_iter)
    elapsed = time.perf_counter() - start
    print(f"Computed in {elapsed:.2f}s", flush=True)

    pixels = (255 * counts / args.max_iter).astype(np.uint8)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="L").save(args.out)
    print(f"Wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
