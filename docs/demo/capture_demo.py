"""Generate a 72-second public portfolio GIF from the local HTML demo.

The frames contain sample data only and are visibly marked as a public demo.
No application credentials or merchant data are read by this script.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
HTML = ROOT / "index.html"
FRAMES = ROOT / "frames"
OUTPUT = ROOT / "tiktokshop-auto-listing-demo.gif"
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def find_edge() -> Path:
    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Microsoft Edge was not found in the expected locations")


def capture_frames() -> list[Path]:
    edge = find_edge()
    FRAMES.mkdir(parents=True, exist_ok=True)
    frame_paths: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="portfolio-demo-edge-") as profile_dir:
        for step in range(1, 7):
            output = FRAMES / f"step-{step}.png"
            url = f"{HTML.as_uri()}?step={step}"
            subprocess.run(
                [
                    str(edge),
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={profile_dir}",
                    "--force-device-scale-factor=1",
                    "--window-size=1280,720",
                    f"--screenshot={output}",
                    url,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            frame_paths.append(output)
    return frame_paths


def build_gif(frame_paths: list[Path]) -> None:
    frames = [Image.open(path).convert("P", palette=Image.Palette.ADAPTIVE, colors=128) for path in frame_paths]
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=[12000] * len(frames),
        loop=0,
        optimize=True,
        disposal=2,
    )


if __name__ == "__main__":
    paths = capture_frames()
    build_gif(paths)
    print(f"Generated: {OUTPUT}")
