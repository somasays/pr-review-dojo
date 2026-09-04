"""Hidden test: OrderService depends on the PaymentGateway protocol, not a concrete client."""

import inspect

from app.services import order_service
from app.services.order_service import OrderService
from app.services.payments import PaymentGateway


def test_constructor_takes_a_gateway_protocol_with_no_concrete_default():
    params = inspect.signature(OrderService.__init__, eval_str=True).parameters
    assert params["gateway"].annotation is PaymentGateway
    assert params["gateway"].default is inspect.Parameter.empty
    assert "InMemoryGateway" not in vars(order_service)
