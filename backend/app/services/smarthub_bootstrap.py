"""Heslo používateľov (bcrypt) a prvotné vytvorenie admina z prostredia."""

from __future__ import annotations

import os

import bcrypt
from sqlmodel import Session, select

from app.models.entities import (
    Offer,
    OfferLine,
    ProductList,
    ProductListItem,
    SmarthubUser,
    Supplier,
    UserSupplierCredential,
)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


def seed_initial_admin_if_empty(session: Session) -> None:
    """Ak DB nemá používateľov, vytvor admina z SMARTHUB_ADMIN_USERNAME / PASSWORD a skopíruj údaje dodávateľov."""
    if session.exec(select(SmarthubUser)).first() is not None:
        return
    u = (os.environ.get("SMARTHUB_ADMIN_USERNAME") or "").strip()
    p = os.environ.get("SMARTHUB_ADMIN_PASSWORD") or ""
    if not u or not p:
        return
    admin = SmarthubUser(
        username=u,
        password_hash=hash_password(p),
        is_admin=True,
        display_label="Administrátor",
    )
    session.add(admin)
    session.flush()
    for sup in session.exec(select(Supplier)).all():
        session.add(
            UserSupplierCredential(
                user_id=admin.id,
                supplier_id=int(sup.id),
                username=sup.username or "",
                password=sup.password or "",
            )
        )


def ensure_credentials_for_supplier(session: Session, supplier_id: int) -> None:
    """Po pridaní dodávateľa doplní riadky poverení pre všetkých existujúcich používateľov."""
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        return
    users = session.exec(select(SmarthubUser)).all()
    for user in users:
        exists = session.exec(
            select(UserSupplierCredential).where(
                UserSupplierCredential.user_id == user.id,
                UserSupplierCredential.supplier_id == supplier_id,
            )
        ).first()
        if exists is None:
            session.add(
                UserSupplierCredential(
                    user_id=user.id,
                    supplier_id=supplier_id,
                    username=supplier.username or "",
                    password=supplier.password or "",
                )
            )


def copy_supplier_credentials_for_new_user(session: Session, user_id: int) -> None:
    for sup in session.exec(select(Supplier)).all():
        session.add(
            UserSupplierCredential(
                user_id=user_id,
                supplier_id=int(sup.id),
                username=sup.username or "",
                password=sup.password or "",
            )
        )


def delete_smarthub_user_cascade(session: Session, user_id: int) -> None:
    """Vymaže používateľa a všetky jeho dáta (poverenia, zoznamy, ponuky)."""
    for cred in session.exec(
        select(UserSupplierCredential).where(UserSupplierCredential.user_id == user_id)
    ).all():
        session.delete(cred)

    for offer in session.exec(select(Offer).where(Offer.user_id == user_id)).all():
        for line in session.exec(
            select(OfferLine).where(OfferLine.offer_id == offer.id)
        ).all():
            session.delete(line)
        session.delete(offer)

    for pl in session.exec(select(ProductList).where(ProductList.user_id == user_id)).all():
        for item in session.exec(
            select(ProductListItem).where(ProductListItem.list_id == pl.id)
        ).all():
            session.delete(item)
        session.delete(pl)

    user = session.get(SmarthubUser, user_id)
    if user is not None:
        session.delete(user)
