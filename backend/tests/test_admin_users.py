"""Test mazania používateľa pobočky (kaskáda)."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Offer,
    OfferLine,
    Product,
    ProductList,
    ProductListItem,
    SmarthubUser,
    Supplier,
    UserSupplierCredential,
)
from app.services.smarthub_bootstrap import delete_smarthub_user_cascade, hash_password


def _session_with_branch_user() -> tuple[Session, int]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    user = SmarthubUser(
        username="pobočka",
        password_hash=hash_password("heslo"),
        is_admin=False,
        display_label="Test",
    )
    session.add(user)
    session.flush()
    user_id = int(user.id)
    supplier = Supplier(
        name="Test",
        shop_url="https://example.com",
        username="",
        password="",
    )
    session.add(supplier)
    session.flush()
    product = Product(internal_code="ABC")
    session.add(product)
    session.flush()
    session.add(
        UserSupplierCredential(
            user_id=user_id,
            supplier_id=int(supplier.id),
            username="u",
            password="p",
        )
    )
    pl = ProductList(user_id=user_id, name="Zoznam")
    session.add(pl)
    session.flush()
    session.add(ProductListItem(list_id=int(pl.id), product_id=int(product.id)))
    offer = Offer(user_id=user_id, offer_number="CP-001", title="Ponuka")
    session.add(offer)
    session.flush()
    session.add(
        OfferLine(
            offer_id=int(offer.id),
            position=1,
            description="Položka",
            quantity=1,
            unit="ks",
            unit_price_eur=1,
        )
    )
    session.commit()
    return session, user_id


def test_delete_smarthub_user_cascade_removes_related_data() -> None:
    session, user_id = _session_with_branch_user()
    try:
        delete_smarthub_user_cascade(session, user_id)
        session.commit()
        assert session.get(SmarthubUser, user_id) is None
        assert (
            session.exec(
                select(UserSupplierCredential).where(
                    UserSupplierCredential.user_id == user_id
                )
            ).first()
            is None
        )
        assert (
            session.exec(
                select(ProductList).where(ProductList.user_id == user_id)
            ).first()
            is None
        )
        assert (
            session.exec(select(Offer).where(Offer.user_id == user_id)).first()
            is None
        )
    finally:
        session.close()
