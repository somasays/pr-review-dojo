import pytest

from app.services.pricing_service import DiscountExhausted, PricingService, UnknownDiscountCode
from conftest import ADMIN_KEY

A = {"X-API-Key": ADMIN_KEY}


def test_codes_resolve_from_the_database(db, seeded):
    svc = PricingService(db)
    assert [d.code for d in svc.resolve_discounts([" flat5 "])] == ["FLAT5"]
    with pytest.raises(UnknownDiscountCode):
        svc.resolve_discounts(["NOPE"])


def test_code_at_its_limit_is_rejected(db, seeded):
    row = seeded["discounts"]["WELCOME10"]
    row.max_redemptions, row.times_redeemed = 1, 1
    db.commit()
    with pytest.raises(DiscountExhausted):
        PricingService(db).resolve_discounts(["WELCOME10"])


def test_admin_can_list_create_and_deactivate_codes(client):
    listed = client.get("/discounts", headers=A).json()
    assert [d["code"] for d in listed] == ["BULK15", "FLAT5", "WELCOME10"]
    body = {"code": "spring20", "kind": "percent", "value": "20", "max_redemptions": 5}
    created = client.post("/discounts", json=body, headers=A)
    assert created.status_code == 201 and created.json()["code"] == "SPRING20"
    assert client.post("/discounts/spring20/deactivate", headers=A).json()["active"] is False


def test_import_adds_a_batch_of_new_codes(client):
    body = [{"code": "autumn10", "kind": "percent", "value": "10"}]
    resp = client.post("/discounts/import", json=body, headers=A)
    assert resp.status_code == 200 and resp.json() == []
    assert "AUTUMN10" in [d["code"] for d in client.get("/discounts", headers=A).json()]
