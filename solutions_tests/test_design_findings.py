import ast
import inspect
from pathlib import Path

from app.services import notification
from app.services.order_service import OrderService


def test_notification_service_has_a_pure_formatting_function():
    assert notification.format_shipped_body("T1") == (
        "Your order is on the way. Tracking number: T1."
    )
    assert notification.format_shipped_body(None) == "Your order is on the way."
    params = list(inspect.signature(notification.NotificationService.__init__).parameters)
    assert params == ["self", "sender", "settings"]


def test_fulfill_batch_takes_a_job_type_not_bare_tuples():
    param = inspect.signature(OrderService.fulfill_batch).parameters["jobs"]
    assert "FulfillmentJob" in str(param.annotation)


def test_mark_paid_and_ship_share_a_private_helper():
    tree = ast.parse(inspect.getsource(OrderService))
    class_node = tree.body[0]
    methods = {n.name: n for n in class_node.body if isinstance(n, ast.FunctionDef)}

    def calls_move_once(node: ast.FunctionDef) -> bool:
        return any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "_move_once"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )

    assert calls_move_once(methods["mark_paid"])
    assert calls_move_once(methods["ship"])


def test_release_has_a_direct_test_in_the_shipped_suite():
    shipped = "\n".join(p.read_text() for p in Path("tests").glob("*.py"))
    assert ".release(" in shipped
