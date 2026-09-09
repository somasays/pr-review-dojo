from app.services.config import Settings
from app.services.webhooks import WebhookDispatcher, WebhookEndpoint, WebhookEvent

EVENT = WebhookEvent(id="evt-1", kind="order.paid", data={"order_id": 7})
SETTINGS = Settings(webhook_attempts=2, notify_backoff_seconds=0.0)
HOOK_A = WebhookEndpoint("https://a.example/hook")
HOOK_B = WebhookEndpoint("https://b.example/hook")


class StubTransport:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url: str, body: dict) -> int:
        self.posts.append((url, body))
        return self.status


async def test_fan_out_posts_to_every_endpoint():
    transport = StubTransport()
    dispatcher = WebhookDispatcher(transport, SETTINGS)

    results = await dispatcher.fan_out(EVENT, [HOOK_A, HOOK_B])

    assert [r.ok for r in results] == [True, True]
    assert sorted(url for url, _ in transport.posts) == [HOOK_A.url, HOOK_B.url]
    assert transport.posts[0][1] == {
        "event_id": "evt-1",
        "kind": "order.paid",
        "data": {"order_id": 7},
    }
