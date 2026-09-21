"""Création des utilisateurs : `python -m app.auth.cli create-user LOGIN --role admin`.

Le mot de passe est lu sur `NEW_USER_PASSWORD` ou demandé au terminal (jamais en argument).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.common.config import get_settings
from app.common.models import RoleName
from app.db.models import Role, User
from app.db.session import create_engine, create_session_factory

MIN_PASSWORD_LENGTH = 10


async def create_user(login: str, role: str, password: str, *, update: bool = False) -> str:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            role_id = await session.scalar(select(Role.id).where(Role.name == role))
            if role_id is None:
                raise SystemExit(f"rôle inconnu : {role} (migrations appliquées ?)")
            user = await session.scalar(select(User).where(User.login == login))
            if user is not None and not update:
                raise SystemExit(f"l'utilisateur {login} existe déjà (--update pour le modifier)")
            if user is None:
                session.add(
                    User(
                        login=login,
                        password_hash=hash_password(password),
                        role_id=role_id,
                        active=True,
                    )
                )
                return "créé"
            user.password_hash = hash_password(password)
            user.role_id = role_id
            user.active = True
            return "mis à jour"
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.auth.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="crée (ou met à jour) un utilisateur")
    create.add_argument("login")
    create.add_argument("--role", choices=[r.value for r in RoleName], default="viewer")
    create.add_argument("--update", action="store_true", help="modifier un utilisateur existant")
    args = parser.parse_args(argv)

    password = os.environ.get("NEW_USER_PASSWORD") or getpass.getpass("Mot de passe : ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"mot de passe trop court (minimum {MIN_PASSWORD_LENGTH} caractères)")
    outcome = asyncio.run(create_user(args.login, args.role, password, update=args.update))
    print(f"utilisateur {args.login} ({args.role}) {outcome}")


if __name__ == "__main__":
    main()
