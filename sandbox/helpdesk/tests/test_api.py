from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sandbox.helpdesk.tests.conftest import AGENT_KEY, LEAD_KEY

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).isoformat()


def test_api_create_claim_resolve_flow(client: TestClient) -> None:
    created = client.post(
        "/tickets",
        json={"subject": "the printer is on fire", "priority": "high", "now": NOW},
        headers={"X-Helpdesk-Key": AGENT_KEY},
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]

    claimed = client.post(
        f"/tickets/{ticket_id}/claim",
        json={"now": NOW},
        headers={"X-Helpdesk-Key": AGENT_KEY},
    )
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "claimed"

    queue = client.get("/queue", headers={"X-Helpdesk-Key": AGENT_KEY})
    assert ticket_id not in [t["id"] for t in queue.json()]

    resolved = client.post(
        f"/tickets/{ticket_id}/resolve",
        json={"now": NOW},
        headers={"X-Helpdesk-Key": LEAD_KEY},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    metrics = client.get("/metrics", headers={"X-Helpdesk-Key": LEAD_KEY})
    assert metrics.status_code == 200
    assert metrics.json() == {"created": 1, "claimed": 1, "resolved": 1, "assigned": 0}


def test_api_metrics_requires_lead(client: TestClient) -> None:
    response = client.get("/metrics", headers={"X-Helpdesk-Key": AGENT_KEY})
    assert response.status_code == 403
