"""API tests: auth roles and advertiser scoping."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sandbox.adserver.db import Advertiser, Campaign
from sandbox.adserver.tests.conftest import (
    ADVERTISER_KEY,
    OPS_KEY,
    OTHER_ADVERTISER_KEY,
)


def test_create_campaign_requires_ops_key_then_the_owning_advertiser_can_see_it(
    client: TestClient, advertiser: Advertiser
):
    denied = client.post(
        "/campaigns",
        json={
            "advertiser_email": advertiser.email,
            "name": "Spring Sale",
            "daily_budget": "5.00",
            "cpm": "1.00",
        },
        headers={"X-Adserver-Key": ADVERTISER_KEY},
    )
    assert denied.status_code == 403

    created = client.post(
        "/campaigns",
        json={
            "advertiser_email": advertiser.email,
            "name": "Spring Sale",
            "daily_budget": "5.00",
            "cpm": "1.00",
        },
        headers={"X-Adserver-Key": OPS_KEY},
    )
    assert created.status_code == 201
    campaign_id = created.json()["id"]

    spend = client.get(
        f"/campaigns/{campaign_id}/spend", headers={"X-Adserver-Key": ADVERTISER_KEY}
    )
    assert spend.status_code == 200
    assert spend.json()["campaign_id"] == campaign_id


def test_advertiser_cannot_see_another_advertisers_campaign_spend(
    client: TestClient, campaign: Campaign
):
    resp = client.get(
        f"/campaigns/{campaign.id}/spend", headers={"X-Adserver-Key": OTHER_ADVERTISER_KEY}
    )
    assert resp.status_code == 403


def test_serve_endpoint_requires_ops_and_returns_a_serve_bool(
    client: TestClient, campaign: Campaign
):
    denied = client.post(f"/serve/{campaign.id}", headers={"X-Adserver-Key": ADVERTISER_KEY})
    assert denied.status_code == 403

    served = client.post(f"/serve/{campaign.id}", headers={"X-Adserver-Key": OPS_KEY})
    assert served.status_code == 200
    assert served.json() == {"serve": True}
