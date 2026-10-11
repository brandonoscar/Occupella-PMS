"""Server-side commands for whoever runs the PMS: add a staff member and invite them. This is
how the first staff member gets in; after that, staff invite each other through the API.

usage:
  python -m pms.admin add-staff <pmc_id> <name>
  python -m pms.admin invite <pmc_id> {staff,owner,tenant} <person_id> <email>

Connects with DATABASE_URL, acting as trust_app (or --role). The invite link goes to standard
output: send it to the person yourself. PMS_PUBLIC_URL is the address people open it at.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from uuid import UUID

import psycopg
from psycopg import sql

from pms import accounts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pms.admin", description=__doc__.split("\n")[0])
    parser.add_argument("--role", default="trust_app", help="database role to act as")
    commands = parser.add_subparsers(dest="command", required=True)
    staff = commands.add_parser("add-staff", help="add a staff member to a PMC")
    staff.add_argument("pmc_id", type=UUID)
    staff.add_argument("name")
    invite = commands.add_parser("invite", help="print an invite link for a person")
    invite.add_argument("pmc_id", type=UUID)
    invite.add_argument("kind", choices=sorted(accounts.KINDS))
    invite.add_argument("person_id", type=UUID)
    invite.add_argument("email")
    options = parser.parse_args(argv)
    public_url = os.environ.get("PMS_PUBLIC_URL", "http://localhost:8000").rstrip("/")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(options.role)))
        try:
            if options.command == "add-staff":
                row = conn.execute(
                    "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
                    (options.pmc_id, options.name),
                ).fetchone()
                assert row is not None
                print(row[0])
            else:
                made = accounts.create_invite(
                    conn, options.pmc_id, options.kind, options.person_id, options.email, None
                )
                print(f"{public_url}/invite#{made.token}")
                print(f"expires {made.expires_at.isoformat()}", file=sys.stderr)
        except (accounts.Refused, psycopg.errors.ForeignKeyViolation) as refused:
            conn.rollback()
            print(f"refused: {str(refused).splitlines()[0]}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
