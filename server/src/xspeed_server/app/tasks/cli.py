"""Thin CLI wrapper for the ``task_results`` archive mover.

Lives in its own module so that ``python -m xspeed_server.app.tasks.cli``
does not trigger Python's "found in sys.modules after import of
package" warning (which would otherwise fire when the same file is
both imported via :mod:`xspeed_server.app.tasks.__init__` and run as
``__main__``).

The Makefile target ``make archive`` invokes this entry point.
"""

from __future__ import annotations

from xspeed_server.app.tasks.archive import run_cli


def main() -> int:
    """Synchronous wrapper around :func:`archive.run_cli`."""
    return run_cli()


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())


__all__ = ["main"]
