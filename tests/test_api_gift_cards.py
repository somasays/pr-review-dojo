from decimal import Decimal

from app.db.models import GiftCard
from conftest import CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}


def test_get_balance_returns_code_and_amount(client, db):
    card = GiftCard(code="GIFT100", balance=Decimal("25.00"), currency="USD")
    db.add(card)
    db.commit()

    r = client.get("/gift-cards/GIFT100/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "GIFT100"
    assert body["balance"] == "25.00"


def test_unknown_code_returns_404(client):
    r = client.get("/gift-cards/NOPE/balance")
    assert r.status_code == 404


def test_create_order_redeems_gift_card(client, db):
    card = GiftCard(code="BIGCARD", balance=Decimal("500.00"), currency="USD")
    db.add(card)
    db.commit()

    r = client.post(
        "/orders",
        json={
            "idempotency_key": "giftcard-key-001",
            "items": [{"sku": "WIDGET", "quantity": 1}],
            "gift_card_code": "BIGCARD",
        },
        headers=H,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["gift_card_code"] == "BIGCARD"
    assert Decimal(body["gift_card_redeemed"]) == Decimal(body["total"])
    assert Decimal(body["remaining_charge"]) == Decimal("0.00")

    balance = client.get("/gift-cards/BIGCARD/balance").json()
    assert Decimal(balance["balance"]) == Decimal("500.00") - Decimal(body["gift_card_redeemed"])
