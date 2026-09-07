"""Reuse the sandbox/rooms fixtures for the exercise 36 hidden tests."""

from sandbox.rooms.tests.conftest import (  # noqa: F401
    HOLDER_EMAIL,
    TEST_API_KEY,
    client,
    db,
    engine,
    room,
    session_factory,
)
