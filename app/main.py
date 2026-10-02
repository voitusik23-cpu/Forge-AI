"""Minimal Forge AI startup and health check."""

from app.runtime.bootstrap import create_runtime


def main() -> int:
    """Build the local runtime and report a healthy startup status."""
    create_runtime()
    print("Forge AI is running (health check: OK).")
    return 0
