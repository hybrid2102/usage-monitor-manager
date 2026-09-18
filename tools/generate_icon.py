"""Generate ``assets/UsageMonitorManager.ico`` from the tray glyph.

One drawing, two consumers: the tray builds the image at runtime with Pillow
(:func:`usage_monitor_manager.ui.tray.draw_glyph`) and the packaged executable
carries the same glyph as a multi-resolution ``.ico``. Keeping the icon
generated instead of committing a binary means the two can never drift.

Usage::

    .venv\\Scripts\\python tools\\generate_icon.py [output.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from usage_monitor_manager.ui.tray import draw_glyph  # noqa: E402 - after the path setup

SIZES = (16, 24, 32, 48, 64, 128, 256)
DEFAULT_OUTPUT = REPO_ROOT / "assets" / "UsageMonitorManager.ico"


def generate(output: Path = DEFAULT_OUTPUT) -> Path:
    """Write the multi-resolution icon and return its path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    largest = draw_glyph(max(SIZES))
    largest.save(output, format="ICO", sizes=[(size, size) for size in SIZES])
    return output


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    output = Path(args[0]) if args else DEFAULT_OUTPUT
    path = generate(output)
    print(f"icona scritta in {path} ({', '.join(f'{s}x{s}' for s in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
