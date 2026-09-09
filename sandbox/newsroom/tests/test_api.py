"""API-level tests: roles and the end-to-end publish/place/home flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from sandbox.newsroom.db import Article
from sandbox.newsroom.domain.curation import ArticleStatus
from sandbox.newsroom.tests.conftest import EDITOR_KEY, READER_KEY

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = T0 + timedelta(hours=2)


def test_reader_key_cannot_publish(client: TestClient):
    response = client.post(
        "/articles/1/publish", json={"now": T0.isoformat()}, headers={"X-Newsroom-Key": READER_KEY}
    )
    assert response.status_code == 403


def test_editor_publishes_and_places_then_reader_sees_it_on_home(client: TestClient, section):
    from sandbox.newsroom import api

    session = api.app.state.refresher.session_factory()
    try:
        article = Article(headline="Big News", status=ArticleStatus.DRAFT.value)
        session.add(article)
        session.commit()
        article_id = article.id
    finally:
        session.close()

    publish = client.post(
        f"/articles/{article_id}/publish",
        json={"now": T0.isoformat()},
        headers={"X-Newsroom-Key": EDITOR_KEY},
    )
    assert publish.status_code == 200

    place = client.post(
        f"/sections/{section.id}/placements",
        json={
            "slot": 1,
            "article_id": article_id,
            "window_start": T0.isoformat(),
            "window_end": T2.isoformat(),
        },
        headers={"X-Newsroom-Key": EDITOR_KEY},
    )
    assert place.status_code == 201

    before = client.get(f"/home/{section.id}", headers={"X-Newsroom-Key": READER_KEY})
    assert before.status_code == 503

    api.app.state.refresher.run_once(T0)

    after = client.get(f"/home/{section.id}", headers={"X-Newsroom-Key": READER_KEY})
    assert after.status_code == 200
    assert after.json()[0]["article_id"] == article_id
