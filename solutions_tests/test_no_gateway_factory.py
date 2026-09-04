"""Hidden test: no factory or registry for the single payment gateway implementation."""

from app.services import payments


def test_payments_module_has_no_gateway_factory():
    names = vars(payments)
    assert not any(n.startswith(("make_", "create_", "build_")) for n in names)
    type_dicts = [
        v
        for v in names.values()
        if isinstance(v, dict) and v and all(isinstance(x, type) for x in v.values())
    ]
    assert not type_dicts
