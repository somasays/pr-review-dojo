"""Covers the Blocker: the balance lookup must require a valid API key."""

from decimal import Decimal

from app.db.models import GiftCard


def test_balance_lookup_requires_authentication(client, db):
    card = GiftCard(code="NOAUTH1", balance=Decimal("40.00"), currency="USD")
    db.add(card)
    db.commit()

    r = client.get("/gift-cards/NOAUTH1/balance")

    assert r.status_code == 401
