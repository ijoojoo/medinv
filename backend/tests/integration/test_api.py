import json
import os
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, text

from medinv.entrypoints.core_api import create_app

pytestmark = pytest.mark.integration
CONTRACT = json.loads(
    (Path(__file__).parents[3] / "contracts/openapi/core-openapi.json").read_text(
        encoding="utf-8-sig"
    )
)


def validate_response(response, schema):
    validator = Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
        format_checker=FormatChecker(),
    )
    validator.validate(response.json())


@pytest.fixture
def api(world, settings):
    token, csrf = token_urlsafe(48), token_urlsafe(48)
    owner = create_engine(os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True)
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "INSERT INTO control.tenant(id,name) VALUES (:id,'API test') ON CONFLICT DO NOTHING"
            ),
            {"id": world.ctx.tenant_id},
        )
        conn.execute(
            text("""INSERT INTO control.principal(id,issuer,subject,display_name)
            VALUES (:id,'test',CAST(:id AS text),'API test') ON CONFLICT DO NOTHING"""),
            {"id": world.ctx.principal_id},
        )
        conn.execute(
            text("""INSERT INTO control.membership
            (tenant_id,principal_id,entity_ids,permissions) VALUES (:tenant,:actor,
                :entities,:permissions)"""),
            {
                "tenant": world.ctx.tenant_id,
                "actor": world.ctx.principal_id,
                "entities": list(world.ctx.entity_ids),
                "permissions": list(world.ctx.permissions),
            },
        )
        conn.execute(
            text("""INSERT INTO control.web_session
            (token_hash,tenant_id,principal_id,csrf_hash,context_generation,
                permission_version,expires_at)
            VALUES (:token,:tenant,:actor,:csrf,1,1,now()+interval '1 hour')"""),
            {
                "token": sha256(token.encode()).hexdigest(),
                "tenant": world.ctx.tenant_id,
                "actor": world.ctx.principal_id,
                "csrf": sha256(csrf.encode()).hexdigest(),
            },
        )
    with TestClient(create_app(settings)) as client:
        client.cookies.set("MED_SESSION", token)
        client.headers.update(
            {
                "X-CSRF-Token": csrf,
                "Origin": "http://127.0.0.1:5173",
                "X-Context-Generation": "1",
                "Idempotency-Key": str(uuid4()),
            }
        )
        yield client, owner
    owner.dispose()


def test_api_round_trip_matches_contract(api, world):
    client, _ = api
    assert client.get("/health/ready").status_code == 200
    context = client.get("/api/v1/context")
    assert context.status_code == 200
    validate_response(context, "ContextResponse")
    assert context.json()["data"]["tenant_id"] == str(world.ctx.tenant_id)
    snapshot = client.get(
        "/api/v1/inventory/availability",
        params={
            "entity_id": str(world.entity),
            "warehouse_id": str(world.warehouse),
            "sku_id": str(world.sku),
        },
    )
    validate_response(snapshot, "AvailabilityResponse")
    request = world.demand("2")
    response = client.post("/api/v1/reservations", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.json()
    validate_response(response, "ReservationResultResponse")
    release = client.post(
        f"/api/v1/reservations/{response.json()['data']['reservation_id']}/release",
        json={"expected_version": "1", "reason": "测试取消"},
    )
    assert release.status_code == 200, release.json()
    validate_response(release, "StateResultResponse")
    assert release.headers["Cache-Control"] == "no-store"


def test_new_openapi_operations_keep_generated_schemas_and_command_headers(api):
    client, _ = api
    schema = client.get("/openapi.json").json()
    for name in ("ProductCreate", "PartyCreate", "InspectionCreate", "OrderCreate"):
        assert name in schema["components"]["schemas"]
    parameters = schema["paths"]["/api/v1/catalog/products"]["post"]["parameters"]
    assert {p["name"] for p in parameters if p["in"] == "header"} == {
        "X-Context-Generation",
        "X-CSRF-Token",
        "Idempotency-Key",
        "Origin",
    }

    def resolve(value):
        if isinstance(value, dict):
            if "$ref" in value and value["$ref"].startswith("#/"):
                target = schema
                for part in value["$ref"][2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                resolve(child)
        elif isinstance(value, list):
            for child in value:
                resolve(child)

    resolve(schema)


def test_cannot_forge_tenant_by_header_or_body(api, world):
    client, _ = api
    client.headers["X-Tenant-ID"] = str(uuid4())
    assert client.get("/api/v1/context").json()["data"]["tenant_id"] == str(world.ctx.tenant_id)
    body = world.demand("1").model_dump(mode="json")
    body["tenant_id"] = str(uuid4())
    response = client.post("/api/v1/reservations", json=body)
    assert response.status_code == 422
    validate_response(response, "ErrorResponse")
    assert world.scalar("SELECT count(*) FROM med.reservation") == 0


@pytest.mark.parametrize(
    "header,value,status",
    [
        ("X-CSRF-Token", "wrong", 403),
        ("X-Context-Generation", "2", 409),
        ("Idempotency-Key", "short", 400),
    ],
)
def test_write_headers_enforced(api, world, header, value, status):
    client, _ = api
    client.headers[header] = value
    response = client.post("/api/v1/reservations", json=world.demand("1").model_dump(mode="json"))
    assert response.status_code == status
    validate_response(response, "ErrorResponse")
    assert world.scalar("SELECT count(*) FROM med.reservation") == 0


def test_revocation_and_permission_version_are_checked_on_each_request(api, world):
    client, owner = api
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permission_version=2 WHERE tenant_id=:tenant"),
            {"tenant": world.ctx.tenant_id},
        )
    assert client.get("/api/v1/context").status_code == 401
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text("UPDATE control.membership SET permission_version=1 WHERE tenant_id=:tenant"),
            {"tenant": world.ctx.tenant_id},
        )
        conn.execute(
            text("UPDATE control.web_session SET revoked_at=now() WHERE tenant_id=:tenant"),
            {"tenant": world.ctx.tenant_id},
        )
    assert client.get("/api/v1/context").status_code == 401


def test_anonymous_and_no_permission_fail_closed(api, world):
    client, owner = api
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=ARRAY[]::text[] WHERE tenant_id=:tenant"
            ),
            {"tenant": world.ctx.tenant_id},
        )
    assert (
        client.post(
            "/api/v1/reservations", json=world.demand("1").model_dump(mode="json")
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.get("/api/v1/context").status_code == 401


def test_published_openapi_has_only_implemented_operations_and_contract_cookie(api):
    client, _ = api
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert len(schema["paths"]) == 153
    assert set(schema["paths"]["/api/v1/catalog/field-rules/{category}"]) == {"get", "post"}
    assert schema["components"]["securitySchemes"]["SessionCookie"]["name"] == "MED_SESSION"
    assert "/api/v1/receipts/{id}/post" not in schema["paths"]


def test_warehouse_http_read_scope_and_write_permission(api, world):
    client, _ = api
    response = client.get("/api/v1/warehouses", params={"entity_id": str(world.entity)})
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["id"] == str(world.warehouse)
    assert client.get(f"/api/v1/warehouses/{world.warehouse}").status_code == 200
    assert client.get("/api/v1/warehouses", params={"entity_id": str(uuid4())}).status_code == 404
    assert (
        client.get(
            "/api/v1/warehouses", params={"entity_id": str(world.entity), "offset": -1}
        ).status_code
        == 422
    )
    response = client.post(
        f"/api/v1/warehouses/{world.warehouse}/locations",
        json={
            "client_operation_id": str(uuid4()),
            "expected_version": "1",
            "code": "NO-PERMISSION",
            "name": "拒绝创建",
            "reason": "越权测试",
        },
    )
    assert response.status_code == 403
    assert world.scalar("SELECT count(*) FROM med.location") == 1


def test_warehouse_write_switch_can_be_disabled_independently(settings, world):
    settings = settings.model_copy(
        update={"warehouse_commands_enabled": False, "transfer_commands_enabled": True}
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/warehouses",
            json={
                "client_operation_id": str(uuid4()),
                "entity_id": str(world.entity),
                "code": "DISABLED",
                "name": "停写测试",
                "location_code": "L1",
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "FEATURE_NOT_ENABLED"


def test_packaging_routes_are_authenticated_and_writes_default_off(api, settings, world):
    client, owner = api
    path = f"/api/v1/catalog/skus/{world.sku}/packaging"
    assert client.get(path).status_code == 403
    with owner.begin() as conn:
        conn.execute(text("SET ROLE control_owner"))
        conn.execute(
            text(
                "UPDATE control.membership SET permissions=ARRAY['catalog.read'] "
                "WHERE tenant_id=:tenant"
            ),
            {"tenant": world.ctx.tenant_id},
        )
    assert client.get(path).status_code == 200
    assert client.get(path + "?offset=-1").status_code == 422
    assert client.get(f"/api/v1/catalog/skus/{uuid4()}/packaging").status_code == 404
    response = client.post(
        path,
        json={
            "expected_sku_version": "1",
            "unit_name": "箱",
            "numerator": "12",
            "denominator": "1",
            "reason": "无权建档",
        },
    )
    assert response.status_code == 403
    with TestClient(
        create_app(settings.model_copy(update={"procurement_commands_enabled": False}))
    ) as disabled:
        response = disabled.post(
            path,
            json={
                "expected_sku_version": "1",
                "unit_name": "箱",
                "numerator": "12",
                "denominator": "1",
                "reason": "停写校验",
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "FEATURE_NOT_ENABLED"
