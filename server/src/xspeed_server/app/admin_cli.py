"""Admin user management CLI.

Exposed via :mod:`xspeed_server.app.admin_cli` so the bootstrap
operator can run ``python -m xspeed_server.app.admin_cli create
--email admin@xspeed.ru --password secret123`` to seed the first
administrator. The CLI keeps the :mod:`xspeed_server.app.tasks.cli`
pattern: a thin module-level entry point whose only job is to call
:func:`run_cli` (which owns the asyncio runtime) so the same code
path can be reused from unit tests.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from getpass import getpass

from xspeed_server.app.config import get_settings
from xspeed_server.app.core.security import hash_password
from xspeed_server.app.db import Database

logger = logging.getLogger(__name__)


__all__ = ["create_admin", "main", "run_cli"]


async def create_admin(
    db: Database,
    *,
    email: str,
    password: str,
) -> tuple[str, int]:
    """Insert (or update) an admin record and return ``(status, id)``.

    ``status`` is one of:

    * ``"created"`` — the row did not exist before and was inserted.
    * ``"updated"`` — the row already existed and the password was
      reset to the new value.
    * ``"unchanged"`` — the row already existed and the password
      hash matched the supplied value (no write performed).

    The unique index on ``admins.email`` is the only constraint we
    rely on; everything else flows through plain asyncpg.
    """
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("Invalid email")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    new_hash = hash_password(password)
    async with db.pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, password_hash FROM admins WHERE email = $1",
            normalized,
        )
        if existing is None:
            row = await conn.fetchrow(
                """
                INSERT INTO admins (email, password_hash)
                VALUES ($1, $2)
                RETURNING id
                """,
                normalized,
                new_hash,
            )
            return "created", int(row["id"])

        if str(existing["password_hash"]) == new_hash:
            return "unchanged", int(existing["id"])

        await conn.execute(
            "UPDATE admins SET password_hash = $1 WHERE id = $2",
            new_hash,
            int(existing["id"]),
        )
        return "updated", int(existing["id"])


async def _amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = await Database.create(settings)
    try:
        status, admin_id = await create_admin(
            db, email=args.email, password=args.password
        )
    finally:
        await db.dispose()
    print(f"admin {args.email}: {status} (id={admin_id})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m xspeed_server.app.admin_cli",
        description="Manage XSpeed admin users.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create or update an admin user.")
    create.add_argument("--email", required=True, help="Admin email address.")
    create.add_argument(
        "--password",
        default=None,
        help="Admin password (prompted interactively when omitted).",
    )
    create.add_argument(
        "--stdin",
        action="store_true",
        help="Read the password from stdin (no prompt).",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    """Synchronous entry point used by ``python -m xspeed_server.app.admin_cli``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "create":
        if args.password is None:
            if args.stdin or not sys.stdin.isatty():
                args.password = sys.stdin.readline().rstrip("\n")
            else:
                args.password = getpass("password: ")
    return asyncio.run(_amain(args))


def main() -> int:
    """Console-script entry point."""
    return run_cli()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
