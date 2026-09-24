"""Assemble actual browser captures into a labeled UI tour, without simulating execution.

uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py --output PATH INPUT.png ...
"""

import argparse
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=int, default=2200, help="Milliseconds per capture")
    parser.add_argument("captures", nargs="+")
    args = parser.parse_args()
    if args.duration < 100:
        parser.error("duration must be at least 100 ms")
    frames = []
    for filename in args.captures:
        with Image.open(filename) as source:
            frame = source.convert("RGB")
            frame.thumbnail((1440, 960), Image.Resampling.LANCZOS)
            frames.append(frame)
    size = (max(f.width for f in frames), max(f.height for f in frames))
    aligned = []
    for frame in frames:
        canvas = Image.new("RGB", size, "#f5f8f4")
        canvas.paste(frame, (0, 0))
        aligned.append(canvas)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    aligned[0].save(
        output,
        save_all=True,
        append_images=aligned[1:],
        duration=args.duration,
        loop=0,
        optimize=True,
    )
    print(output)


if __name__ == "__main__":
    main()
