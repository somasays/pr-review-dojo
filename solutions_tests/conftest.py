"""Reuse the sandbox/parking fixtures for the exercise 51 hidden tests."""

from sandbox.parking.tests.conftest import (  # noqa: F401
    ATTENDANT_KEY,
    CARD,
    EPOCH,
    GARAGE_CAPACITY,
    PARKING_KEYS,
    client,
    db,
    engine,
    garage,
    session_factory,
)
