"""
scripts/create_user.py
Provision a Curia user with an API token. Prints the token to stdout.

Usage:
    python scripts/create_user.py --email arihant@sentient.xyz --name arihant
    python scripts/create_user.py --email demo@example.com --name demo --token my-custom-token

The user_id is a UUID. Hand the token to the user — they put it in their
.env or pass it as Authorization: Bearer <token>.
"""

import argparse
import asyncio
import secrets
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from core.db.connection import db_execute, db_fetchrow


def _generate_token(prefix: str = "ck_") -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


async def create_user(
    email: str,
    name: str | None,
    token: str | None,
    role: str = "user",
) -> dict:
    if role not in ("user", "qa"):
        raise ValueError(f"role must be 'user' or 'qa', got {role!r}")
    user_id = str(uuid.uuid4())
    api_token = token or _generate_token()
    await db_execute(
        """
        INSERT INTO users (id, email, name, api_token, role)
        VALUES ($id, $email, $name, $token, $role)
        """,
        {"id": user_id, "email": email, "name": name, "token": api_token, "role": role},
    )
    row = await db_fetchrow(
        "SELECT id, email, name, api_token, role FROM users WHERE id = $id",
        {"id": user_id},
    )
    return dict(row) if row else {}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new Curia user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--token",
        default=None,
        help="Optional explicit token. If omitted, a secure random token is generated.",
    )
    parser.add_argument(
        "--role",
        default="user",
        choices=["user", "qa"],
        help="User role: 'user' (default) or 'qa' (admin/QA access).",
    )
    args = parser.parse_args()

    try:
        user = await create_user(
            email=args.email, name=args.name, token=args.token, role=args.role
        )
    except Exception as e:
        print(f"ERROR creating user: {e}", file=sys.stderr)
        return 1

    print(
        "Created user:\n"
        f"  user_id   : {user['id']}\n"
        f"  email     : {user['email']}\n"
        f"  name      : {user.get('name') or '(none)'}\n"
        f"  role      : {user.get('role') or 'user'}\n"
        f"  api_token : {user['api_token']}\n"
        "\n"
        "Use the api_token via:\n"
        f"  Authorization: Bearer {user['api_token']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
