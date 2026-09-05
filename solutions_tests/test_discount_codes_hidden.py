"""Hidden tests for exercise 11."""

import ast
import inspect
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.routers import discounts as discounts_router
from app.db.models import DiscountCode
from app.db.repositories import DiscountCodeRepository, OrderRepository
from app.domain.money import Money
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService
from conftest import ADMIN_KEY

A = {"X-API-Key": ADMIN_KEY}


def test_discount_value_survives_a_round_trip_as_decimal(db, seeded):
    """SA-04: a money bearing column must be Numeric, not Float."""
    repo = DiscountCodeRepository(db)
    repo.add(DiscountCode(code="CENTS", kind="fixed", value=Decimal("0.10")))
    db.commit()
    db.expunge_all()
    row = repo.by_code("CENTS")
    assert isinstance(row.value, Decimal)
    assert Money.of(row.value).amount == Decimal("0.10")


def test_failed_order_insert_does_not_consume_a_redemption(db, seeded, monkeypatch):
    """SA-02: the redemption must live in the same transaction as the order."""

    def boom(_self, _order, _items):
        raise IntegrityError("insert", None, Exception("duplicate key"))

    monkeypatch.setattr(OrderRepository, "add", boom)
    service = OrderService(
        db, PricingService(db), NotificationService(InMemorySender(), Settings())
    )
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="key-00000077",
        items=[ItemRequest("WIDGET", 1)],
        discount_codes=["FLAT5"],
    )
    with pytest.raises(Exception):
        service.create(cmd)
    db.rollback()
    db.expunge_all()
    assert DiscountCodeRepository(db).by_code("FLAT5").times_redeemed == 0


def test_duplicate_code_is_a_conflict_not_a_server_error(client, monkeypatch):
    """SA-12: losing the check-then-insert race must still answer 409."""
    monkeypatch.setattr(DiscountCodeRepository, "by_code", lambda _self, _code: None)
    body = {"code": "FLAT5", "kind": "fixed", "value": "5"}
    assert client.post("/discounts", json=body, headers=A).status_code == 409
    monkeypatch.undo()
    assert client.get("/discounts", headers=A).status_code == 200


def _import_handler_source() -> str:
    return inspect.getsource(discounts_router.import_discounts)


def test_import_endpoint_does_not_open_its_own_session():
    """SA-10: the handler must use the injected session, not one it opens and never closes."""
    tree = ast.parse(_import_handler_source())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "get_session_factory" not in names


def test_import_endpoint_delegates_writes_to_the_repository():
    """DS-01: the handler must not build rows and write them itself."""
    tree = ast.parse(_import_handler_source())
    attr_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "add" not in attr_calls
    assert "flush" not in attr_calls
    assert "DiscountCodeRepository" in _import_handler_source()


def test_import_endpoint_does_not_call_get_settings_directly():
    """DS-10: settings must arrive by dependency injection, not a manual call."""
    tree = ast.parse(_import_handler_source())
    name_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "get_settings" not in name_calls


def test_import_endpoint_has_no_boolean_mode_flag():
    """DS-11 refactor: no boolean flag switching the endpoint between two behaviors."""
    sig = inspect.signature(discounts_router.import_discounts)
    bool_params = [p for p in sig.parameters.values() if p.annotation is bool]
    assert not bool_params
