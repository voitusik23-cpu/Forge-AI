"""Run the full Forge desktop shell: ``python -m app.desktop``.

Kept separate from :mod:`app.desktop.app` so the widget can open the shell in its
own process with a stable, documented command instead of importing a second Tk
root into a process that already runs one.
"""

from __future__ import annotations

from app.desktop.app import ForgeDesktopApp


def main() -> int:
    app = ForgeDesktopApp()
    app.start()
    return 0


if __name__ == "__main__":  # pragma: no cover - interactive window
    raise SystemExit(main())
