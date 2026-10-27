"""Команды администрирования. Первый администратор создаётся здесь: через API регистрация даёт только роль студента.

  python -m app.cli create-user --email admin@x.ru --role platform_admin
  python -m app.cli create-user --email boss@uni.ru --role org_admin --org-name "СПбГЭУ" --sso-domain unecon.ru
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from .config import get_settings
from .db import make_engine, make_session_factory
from .models import Organization, User
from .security import hash_password


async def create_user(session_factory, *, email: str, password: str, role: str, full_name: str = "",
                      org_name: str | None = None, sso_domain: str | None = None) -> User:
    async with session_factory() as db:
        email = email.lower()
        if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
            raise SystemExit(f"Пользователь {email} уже существует")
        org = None
        if org_name:
            org = (await db.execute(select(Organization).where(Organization.name == org_name))).scalars().first()
            if org is None:
                org = Organization(name=org_name, sso_domain=sso_domain)
                db.add(org)
                await db.flush()
        elif role in ("org_admin", "teacher"):
            raise SystemExit(f"Для роли {role} нужна организация: --org-name")
        user = User(email=email, full_name=full_name or email.split("@")[0], role=role, org_id=org.id if org else None,
                    password_hash=hash_password(password))
        db.add(user)
        await db.commit()
        return user


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=["student", "teacher", "org_admin", "platform_admin"], default="platform_admin")
    cu.add_argument("--full-name", default="")
    cu.add_argument("--org-name")
    cu.add_argument("--sso-domain")
    cu.add_argument("--password", help="если не указан, запросим в терминале")
    args = ap.parse_args(argv)
    password = args.password or getpass.getpass("Пароль (не короче 10 символов): ")
    if len(password) < 10:
        sys.exit("Пароль короче 10 символов")

    async def run() -> None:
        engine = make_engine(get_settings().database_url)
        u = await create_user(make_session_factory(engine), email=args.email, password=password, role=args.role,
                              full_name=args.full_name, org_name=args.org_name, sso_domain=args.sso_domain)
        await engine.dispose()
        print(f"Создан {u.role} {u.email}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
