"""Render the Mandelbrot set to a PNG, optionally split into tiles across many pods.

Modes
  single (default)   render the whole image.
  tile               when SCIRUN_TILE_COUNT is set (SciRun's `parallelism`), render horizontal
                     strip JOB_COMPLETION_INDEX of that many and write it to <out dir>/tiles/.
  --gather           stitch the tiles into the final PNG (safe to run more than once).

Every file is written atomically (temp file, then rename) so a retried or killed pod can never
leave a half-written tile for the gather step to pick up.
"""

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

X_RANGE = (-2.0, 1.0)
Y_RANGE = (-1.2, 1.2)


def mandelbrot(x: np.ndarray, y: np.ndarray, max_iter: int) -> np.ndarray:
    """Return escape-iteration counts, shape (len(y), len(x))."""
    c = x[np.newaxis, :] + 1j * y[:, np.newaxis]
    z = np.zeros_like(c)
    counts = np.zeros(c.shape, dtype=np.int32)
    for i in range(max_iter):
        active = np.abs(z) <= 2
        z[active] = z[active] ** 2 + c[active]
        counts[active] = i
    return counts


def to_pixels(counts: np.ndarray, max_iter: int) -> np.ndarray:
    return (255 * counts / max_iter).astype(np.uint8)


def tile_rows(height: int, tile_count: int, index: int) -> np.ndarray:
    """Row numbers of strip `index`; strips differ by at most one row."""
    return np.array_split(np.arange(height), tile_count)[index]


def atomic_write(path: Path, write) -> None:
    """Call write(file_obj) on a temp file next to `path`, then rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            write(f)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def usage_since(start: float) -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "wall_seconds": time.perf_counter() - start,
        "cpu_seconds": usage.ru_utime + usage.ru_stime,
        "peak_memory_bytes": usage.ru_maxrss * 1024,  # Linux reports KiB
    }


def write_json(path: Path, data: dict) -> None:
    atomic_write(path, lambda f: f.write(json.dumps(data).encode()))


def bump_attempt(tiles_dir: Path, index: int) -> int:
    """Count this run for the tile. Lives in the results volume, so it survives pod retries."""
    marker = tiles_dir / f"tile-{index:03d}.attempts"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    attempt = (int(marker.read_text()) if marker.exists() else 0) + 1
    atomic_write(marker, lambda f: f.write(str(attempt).encode()))
    return attempt


def run_single(args: argparse.Namespace) -> None:
    print(f"Rendering {args.width}x{args.height}, max_iter={args.max_iter}", flush=True)
    start = time.perf_counter()
    x = np.linspace(*X_RANGE, args.width)
    y = np.linspace(*Y_RANGE, args.height)
    counts = mandelbrot(x, y, args.max_iter)
    print(f"Computed in {time.perf_counter() - start:.2f}s", flush=True)

    save_png(args.out, to_pixels(counts, args.max_iter))
    print(f"Wrote {args.out}", flush=True)
    write_json(args.out.parent / "metrics.json", usage_since(start))
    print(f"Wrote {args.out.parent / 'metrics.json'}", flush=True)


def save_png(path: Path, pixels: np.ndarray) -> None:
    atomic_write(path, lambda f: Image.fromarray(pixels, mode="L").save(f, format="PNG"))


def run_tile(args: argparse.Namespace, tile_count: int) -> None:
    index = int(os.environ["JOB_COMPLETION_INDEX"])
    tiles_dir = args.out.parent / "tiles"
    attempt = bump_attempt(tiles_dir, index)
    print(f"tile {index}/{tile_count} attempt {attempt} on {os.environ.get('NODE_NAME', '?')}", flush=True)

    if index in args.fail_tiles and attempt <= args.fail_attempts:
        print(f"injected failure: tile {index}, attempt {attempt}", flush=True)
        sys.exit(1)
    if index == args.slow_tile:
        print(f"injected slowness: sleeping {args.slow_seconds}s", flush=True)
        time.sleep(args.slow_seconds)

    start = time.perf_counter()
    rows = tile_rows(args.height, tile_count, index)
    x = np.linspace(*X_RANGE, args.width)
    y = np.linspace(*Y_RANGE, args.height)[rows]
    pixels = to_pixels(mandelbrot(x, y, args.max_iter), args.max_iter)

    atomic_write(tiles_dir / f"tile-{index:03d}.npy", lambda f: np.save(f, pixels))
    metrics = {**usage_since(start), "node": os.environ.get("NODE_NAME"), "attempt": attempt}
    write_json(tiles_dir / f"tile-{index:03d}.json", metrics)
    print(f"tile {index} done: {pixels.shape[0]} rows in {metrics['wall_seconds']:.2f}s", flush=True)


def run_gather(args: argparse.Namespace, tile_count: int) -> None:
    start = time.perf_counter()
    tiles_dir = args.out.parent / "tiles"
    missing = [i for i in range(tile_count) if not (tiles_dir / f"tile-{i:03d}.npy").exists()]
    if missing:
        print(f"cannot gather: missing tiles {missing}", flush=True)
        sys.exit(3)

    pixels = np.vstack([np.load(tiles_dir / f"tile-{i:03d}.npy") for i in range(tile_count)])
    if pixels.shape[0] != args.height:
        print(f"cannot gather: got {pixels.shape[0]} rows, expected {args.height}", flush=True)
        sys.exit(4)
    save_png(args.out, pixels)
    print(f"stitched {tile_count} tiles into {args.out} ({pixels.shape[1]}x{pixels.shape[0]})", flush=True)

    tile_metrics = [json.loads((tiles_dir / f"tile-{i:03d}.json").read_text()) for i in range(tile_count)]
    summary = {
        "wall_seconds": max(m["wall_seconds"] for m in tile_metrics),  # tiles ran concurrently
        "cpu_seconds": sum(m["cpu_seconds"] for m in tile_metrics),
        "peak_memory_bytes": max(m["peak_memory_bytes"] for m in tile_metrics),
        "tiles": tile_count,
        "nodes": sorted({m["node"] for m in tile_metrics if m.get("node")}),
        "retried_tiles": sum(1 for m in tile_metrics if m.get("attempt", 1) > 1),
        "gather_seconds": time.perf_counter() - start,
    }
    write_json(args.out.parent / "metrics.json", summary)
    print(f"Wrote {args.out.parent / 'metrics.json'}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--out", type=Path, default=Path("results/mandelbrot.png"))
    parser.add_argument("--gather", action="store_true", help="stitch tiles into --out")
    parser.add_argument("--fail-tiles", type=lambda s: {int(i) for i in s.split(",") if i}, default=set(),
                        help="comma-separated tile indexes that crash (failure injection)")
    parser.add_argument("--fail-attempts", type=int, default=1, help="how many attempts of each --fail-tiles tile crash")
    parser.add_argument("--slow-tile", type=int, default=-1, help="tile index that sleeps (straggler injection)")
    parser.add_argument("--slow-seconds", type=float, default=30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tile_count = int(os.environ.get("SCIRUN_TILE_COUNT", "0"))
    if args.gather:
        if tile_count < 1:
            sys.exit("--gather needs SCIRUN_TILE_COUNT")
        run_gather(args, tile_count)
    elif tile_count > 1:
        run_tile(args, tile_count)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
