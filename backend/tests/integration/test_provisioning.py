import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext
from medinv.platform.tenancy.provisioning import EnterprisePlan, EnterpriseProvisioner

pytestmark = pytest.mark.integration


@pytest.fixture
def provisioning(engine, settings):
    control = create_engine(os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True)
    owner, reviewer = uuid4(), uuid4()
    with control.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        for pid in (owner, reviewer):
            conn.execute(
                text("""INSERT INTO control.principal(id,issuer,subject,display_name)
                VALUES(:id,'provision-test:',:subject,'开通测试身份')"""),
                {"id": pid, "subject": str(pid)},
            )
    plan = EnterprisePlan(
        request_id=uuid4(),
        tenant_name="空白企业",
        entity_name="开账主体",
        credit_code="TEST-ONLY",
        warehouse_code="W1",
        warehouse_name="首个仓库",
        location_code="A1",
        reason="软件开账验收",
        members=[
            {"principal_id": owner, "role_codes": ["OWNER"]},
            {"principal_id": reviewer, "role_codes": ["QUALITY"]},
        ],
    )
    uow = TenantUnitOfWork(engine, settings)
    service = EnterpriseProvisioner(control, uow)
    ids = plan.ids()
    ctx = TenantContext(
        ids["tenant"], owner, uuid4(), 1, 1, frozenset({ids["entity"]}), ROLE_PERMISSIONS["OWNER"]
    )

    def state():
        with control.begin() as conn:
            conn.execute(text("SET LOCAL ROLE control_owner"))
            return (
                conn.execute(
                    text("""SELECT t.active,p.state FROM control.tenant t
                JOIN control.enterprise_provisioning p ON p.tenant_id=t.id WHERE t.id=:id"""),
                    {"id": ids["tenant"]},
                )
                .mappings()
                .one_or_none()
            )

    def scalar(sql):
        with uow.open(ctx, readonly=True) as session:
            return session.execute(text(sql)).scalar_one()

    def change(sql, **params):
        with control.begin() as conn:
            conn.execute(text("SET LOCAL ROLE control_owner"))
            conn.execute(text(sql), {"tenant": ids["tenant"], "request": plan.request_id, **params})

    yield SimpleNamespace(**locals())
    control.dispose()


def test_blank_enterprise_has_only_scope_warehouse_members_and_audit(provisioning):
    x = provisioning
    result = x.service.run(x.plan)
    assert result["state"] == "COMPLETED"
    assert result["tenant_id"] == str(x.ids["tenant"])
    assert x.state() == {"active": True, "state": "COMPLETED"}
    for table in ("legal_entity", "warehouse", "location", "audit_event"):
        assert x.scalar(f"SELECT count(*) FROM med.{table}") == 1
    for table in ("product", "party", "stock_balance", "cost_balance", "stock_ledger"):
        assert x.scalar(f"SELECT count(*) FROM med.{table}") == 0
    with x.control.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        members = (
            conn.execute(
                text("SELECT * FROM control.membership WHERE tenant_id=:id"),
                {"id": x.ids["tenant"]},
            )
            .mappings()
            .all()
        )
    assert len(members) == 2
    for member in members:
        assert member["active"] and member["entity_ids"] == [x.ids["entity"]]
        assert set(member["permissions"]) == ROLE_PERMISSIONS[member["role_codes"][0]]


@pytest.mark.parametrize("phase", ["before_business", "after_business", "during_activation"])
def test_interrupted_opening_stays_hidden_then_resumes_without_duplicates(provisioning, phase):
    x = provisioning

    class Interrupted(EnterpriseProvisioner):
        def initialize_business(self, plan):
            if phase == "before_business":
                raise RuntimeError("simulated outage")
            super().initialize_business(plan)
            if phase == "after_business":
                raise RuntimeError("simulated outage")

        def activate(self, conn, plan):
            super().activate(conn, plan)
            raise RuntimeError("simulated outage")

    with pytest.raises(RuntimeError):
        Interrupted(x.control, x.uow).run(x.plan)
    assert x.state() == {"active": False, "state": "PENDING"}
    with x.control.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        assert (
            conn.execute(
                text("""SELECT count(*) FROM control.membership m
            JOIN control.tenant t ON t.id=m.tenant_id WHERE t.id=:id AND m.active AND t.active"""),
                {"id": x.ids["tenant"]},
            ).scalar_one()
            == 0
        )
    assert x.scalar("SELECT count(*) FROM med.legal_entity") == (phase != "before_business")
    assert x.service.run(x.plan)["state"] == "COMPLETED"
    assert x.scalar("SELECT count(*) FROM med.audit_event") == 1


def test_business_transaction_rollback_keeps_pending_and_retries(provisioning, monkeypatch):
    x = provisioning
    import medinv.platform.tenancy.provisioning as module

    original = module.audit

    def failure(*args, **kwargs):
        raise RuntimeError("simulated business commit failure")

    monkeypatch.setattr(module, "audit", failure)
    with pytest.raises(RuntimeError):
        x.service.run(x.plan)
    assert x.scalar("SELECT count(*) FROM med.legal_entity") == 0
    assert x.state()["state"] == "PENDING"
    monkeypatch.setattr(module, "audit", original)
    assert x.service.run(x.plan)["state"] == "COMPLETED"


def test_concurrent_retry_and_replay_preserve_subsequent_administration(provisioning):
    x = provisioning
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: x.service.run(x.plan), range(2)))
    assert results[0] == results[1]
    assert x.scalar("SELECT count(*) FROM med.audit_event") == 1
    x.change("UPDATE control.tenant SET active=false WHERE id=:tenant")
    x.change("UPDATE control.membership SET active=false WHERE tenant_id=:tenant")
    assert x.service.run(x.plan) == results[0]
    assert not x.state()["active"]


def test_conflicting_request_and_unknown_identity_never_activate(provisioning):
    x = provisioning
    unknown = x.plan.model_dump()
    unknown["members"][0]["principal_id"] = uuid4()
    with pytest.raises(BusinessError) as error:
        x.service.run(EnterprisePlan.model_validate(unknown))
    assert error.value.code == "MEMBER_NOT_FOUND" and x.state() is None
    x.service.run(x.plan)
    changed = x.plan.model_dump() | {"tenant_name": "不同企业"}
    with pytest.raises(BusinessError) as error:
        x.service.run(EnterprisePlan.model_validate(changed))
    assert error.value.code == "PROVISION_CONFLICT"


@pytest.mark.parametrize("side", ["control", "business"])
def test_pending_data_mismatch_requires_operator_resolution(provisioning, side):
    x = provisioning

    class StopBeforeActivation(EnterpriseProvisioner):
        def activate(self, conn, plan):
            raise RuntimeError("pause")

    with pytest.raises(RuntimeError):
        StopBeforeActivation(x.control, x.uow).run(x.plan)
    if side == "control":
        x.change("UPDATE control.membership SET entity_ids='{}' WHERE tenant_id=:tenant")
    else:
        with x.uow.open(x.ctx) as session:
            session.execute(text("UPDATE med.legal_entity SET status='SUSPENDED'"))
    with pytest.raises(BusinessError) as error:
        x.service.run(x.plan)
    assert error.value.code == "PROVISION_INCONSISTENT"
    assert x.state() == {"active": False, "state": "PENDING"}


def test_completed_plan_is_immutable_and_normal_role_cannot_provision(provisioning):
    x = provisioning
    x.service.run(x.plan)
    with pytest.raises(DBAPIError):
        x.change("UPDATE control.enterprise_provisioning SET plan='{}' WHERE request_id=:request")
    runtime = create_engine(os.environ["MED_CONTROL_DATABASE_URL"], hide_parameters=True)
    try:
        with pytest.raises(DBAPIError):
            EnterpriseProvisioner(runtime, x.uow).run(x.plan)
    finally:
        runtime.dispose()


@pytest.mark.parametrize("invalid", ["self_review", "duplicate", "unknown_role", "credit_length"])
def test_invalid_plan_is_rejected_before_any_write(provisioning, invalid):
    x = provisioning
    body = x.plan.model_dump()
    if invalid == "self_review":
        for member in body["members"]:
            if "OWNER" not in member["role_codes"]:
                member["role_codes"] = ["VIEWER"]
    elif invalid == "duplicate":
        body["members"].append(body["members"][0])
    elif invalid == "unknown_role":
        body["members"][0]["role_codes"] = ["PLATFORM_ROOT"]
    else:
        body["credit_code"] = "X" * 33
    with pytest.raises(ValidationError):
        EnterprisePlan.model_validate(body)
    assert x.state() is None
