"""Graphical front-end for Shadowguy, built on Arcade.

This is an experimental second client, not a replacement for the Textual TUI. It
imports the same game state and rules (`ShadowguyApp`) and renders a graphical
map/character view; the TUI remains the reference implementation and the test
surface.

Run it from the repo root:

    uv run --group gui python -m shadowguy.gui --seed 12345
    # or, once the gui extra is installed:
    shadowguy-gui --seed 12345
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="shadowguy-gui", description="Shadowguy graphical front-end (Arcade).")
    parser.add_argument("--seed", type=int, default=None, help="run seed (default: random)")
    parser.add_argument("--width", type=int, default=1280, help="window width (default 1280)")
    parser.add_argument("--height", type=int, default=860, help="window height (default 860)")
    args = parser.parse_args(argv)

    try:
        from .window import run
    except ImportError as exc:  # pragma: no cover - depends on the optional group
        raise SystemExit(
            "The graphical front-end needs Arcade.\n"
            "Install it with: uv sync --group gui\n"
            "Then run: uv run --group gui python -m shadowguy.gui"
        ) from exc

    run(seed=args.seed, width=args.width, height=args.height)
