from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from medinv.modules.workspace.templates import EntryTemplates, TemplateSave
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def payload(**extra):
    return TemplateSave.model_validate(
        {
            "expected_version": "0",
            "template": {
                "schema_version": 2,
                "columns": [
                    {
                        "key": key,
                        "label": key,
                        "visible": True,
                        "tab_stop": key in {"product", "qty", "price"},
                        "width_mode": "px",
                        "width": 270,
                    }
                    for key in [
                        "product",
                        "pack",
                        "qty",
                        "price",
                        "tax",
                        "amount",
                        "specification",
                        "manufacturer_name",
                    ]
                ],
            },
            **extra,
        }
    )


def test_templates_private_versioned_idempotent_and_channel_isolated(world):
    ctx = replace(world.ctx, permissions=ROLE_PERMISSIONS["OWNER"])
    service = EntryTemplates(world.uow)
    assert service.detail(ctx, "purchase") == {"version": "0", "template": None}
    body, key = payload(), str(uuid4())
    result = service.save(ctx, "purchase", body, key)
    assert result["version"] == "1"
    assert service.save(ctx, "purchase", body, key) == result
    assert service.detail(ctx, "purchase") == result
    assert service.detail(ctx, "sale")["version"] == "0"
    for outsider in [replace(ctx, principal_id=uuid4()), replace(ctx, tenant_id=uuid4())]:
        assert service.detail(outsider, "purchase")["version"] == "0"
    with pytest.raises(BusinessError, match="其他窗口"):
        service.save(ctx, "purchase", body, str(uuid4()))
    body = body.model_copy(update={"expected_version": "1"})
    assert service.save(ctx, "purchase", body, str(uuid4()))["version"] == "2"
    with pytest.raises(BusinessError, match="其他窗口"):
        service.save(ctx, "purchase", body, str(uuid4()))
    denied = replace(ctx, permissions=frozenset())
    with pytest.raises(BusinessError):
        service.detail(denied, "purchase")
    with pytest.raises(BusinessError):
        service.save(denied, "purchase", body, str(uuid4()))


@pytest.mark.parametrize(
    "field,change",
    [
        ("product", {"visible": False}),
        ("qty", {"tab_stop": False}),
        ("price", {"visible": False}),
        ("amount", {"tab_stop": True}),
        ("product", {"width": 100}),
        ("tax", {"decimals": 2}),
        ("qty", {"decimals": 7}),
        ("qty", {"width_mode": "weight", "width": 21}),
        ("pack", {"label": " "}),
        ("pack", {"tax_display": "percent"}),
        ("tax", {"key": "product"}),
        ("qty", {"width": True}),
    ],
)
def test_invalid_templates_cannot_disable_business_guards(field, change):
    body = payload().model_dump()
    next(c for c in body["template"]["columns"] if c["key"] == field).update(change)
    with pytest.raises(ValidationError):
        TemplateSave.model_validate(body)
