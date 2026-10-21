from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from test_management_workspaces import maintenance, save
from test_party_certificates import revision
from test_procurement import flow as flow
from test_unified_qualification import approve, setup

from medinv.modules.catalog.application.party_certificates import PartyCertificates
from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.workspace.management import Management, MessageRead
from medinv.modules.workspace.reminders import ReminderAction, Reminders
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def inbox(f, source, ctx=None):
    return Reminders(f.world.uow).listing(ctx or f.maker, f.world.entity, source)["items"]


def act(f, row, action="CLAIM", ctx=None, command_key=None, **changes):
    return Reminders(f.world.uow).act(
        ctx or f.maker,
        ReminderAction(
            **{
                "entity_id": f.world.entity,
                "source": row["source"],
                "source_key": row["source_key"],
                "expected_version": row["case_version"],
                "observed_revision": row["source_revision"],
                "action": action,
                "reason": "隔离企业提醒软件测试",
                **changes,
            }
        ),
        command_key or str(uuid4()),
    )


def case(f, row, ctx=None):
    return Reminders(f.world.uow).detail(ctx or f.maker, f.world.entity, UUID(row["case_id"]))


def test_claim_read_assignment_audit_and_verified_closure(flow):
    f = flow
    source = inbox(f, "work")[0]
    key = str(uuid4())
    owned = act(f, source, command_key=key)
    assert act(f, source, command_key=key) == owned
    assert owned["process_state"] == "PROCESSING" and owned["claimed_by_me"]
    assert not inbox(f, "work")[0]["read"]
    Management(f.world.uow).mark_read(
        f.maker,
        MessageRead(entity_id=f.world.entity, source="work", message_id=source["message_id"]),
        str(uuid4()),
    )
    assert inbox(f, "work")[0]["read"]
    assert not inbox(f, "work", f.reviewer)[0]["read"]
    employee = save(f, "staff", {"code": "REM-01", "name": "测试质量负责人"})
    assigned = act(f, owned, "ASSIGN", assigned_staff_id=employee["id"], due_on=f.day)
    assert assigned["assigned_name"] == "测试质量负责人"
    with pytest.raises(BusinessError, match="仍触发"):
        act(f, assigned, "CLOSE")
    f.receive()
    resolved = case(f, assigned)["item"]
    assert resolved["process_state"] == "RESOLVED"
    closed = act(f, resolved, "CLOSE")
    assert closed["process_state"] == "CLOSED" and closed["closed_at"]
    assert len(case(f, closed)["history"]) == 3
    assert Reminders(f.world.uow).cases(f.maker, f.world.entity, "CLOSED", True, 0)["items"]
    assert f.detail()["order"]["approval_state"] == "APPROVED"


def test_read_only_isolation_hidden_sources_and_staff_minimization(flow):
    f = flow
    row = inbox(f, "work")[0]
    viewer = replace(f.maker, permissions=ROLE_PERMISSIONS["VIEWER"])
    assert not inbox(f, "work", viewer)[0]["can_manage"]
    for context in (
        viewer,
        replace(f.maker, entity_ids=frozenset()),
        replace(f.maker, tenant_id=uuid4()),
    ):
        with pytest.raises(BusinessError):
            act(f, row, ctx=context)
    owned = act(f, row)
    cashier = replace(f.maker, permissions=ROLE_PERMISSIONS["CASHIER"])
    assert (
        Reminders(f.world.uow).cases(cashier, f.world.entity, "PROCESSING", False, 0)["items"] == []
    )
    with pytest.raises(BusinessError, match="权限"):
        case(f, owned, cashier)
    employee = save(
        f, "staff", {"code": "REM-02", "name": "负责人", "phone": "PRIVATE", "email": "SECRET"}
    )
    listed = Reminders(f.world.uow).staff(f.reviewer, f.world.entity, "REM-02")["items"][0]
    assert set(listed) == {"id", "code", "name", "job"}
    with pytest.raises(BusinessError):
        act(f, owned, "ASSIGN", assigned_staff_id=uuid4())
    save(
        f,
        "staff",
        {"code": "REM-02", "name": "负责人", "status": "LEFT"},
        UUID(employee["id"]),
        "1",
    )
    with pytest.raises(BusinessError):
        act(f, owned, "ASSIGN", assigned_staff_id=employee["id"])


def test_concurrent_claim_one_wins_stale_case_and_transaction_guard(flow):
    f = flow
    row = inbox(f, "work")[0]

    def claim(context):
        try:
            act(f, row, ctx=context)
            return "ok"
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [f.maker, f.reviewer]))
    assert sorted(results) == ["VERSION_CONFLICT", "ok"]
    current = inbox(f, "work")[0]
    before_count = f.world.scalar(
        "SELECT count(*) FROM med.audit_event WHERE resource_type='reminder_case'"
    )

    def reject():
        raise BusinessError("REVOKED", "权限已撤回", 403)

    token = before_business_commit.set(reject)
    try:
        with pytest.raises(BusinessError, match="已撤回"):
            act(f, current, "NOTE")
    finally:
        before_business_commit.reset(token)
    assert inbox(f, "work")[0]["case_version"] == current["case_version"]
    assert (
        f.world.scalar("SELECT count(*) FROM med.audit_event WHERE resource_type='reminder_case'")
        == before_count
    )


def test_disabled_reminders_cannot_be_used_to_close_unresolved_case(flow):
    f = flow
    row = act(f, inbox(f, "work")[0])
    save(f, "settings", {"reminders_enabled": False, "work_queue_enabled": False})
    assert not inbox(f, "work")
    assert case(f, row)["item"]["condition_present"]
    with pytest.raises(BusinessError, match="仍触发"):
        act(f, row, "CLOSE")


def test_exact_source_beyond_first_page_and_new_work_stage(flow):
    f = flow
    targets = [f.order(approve=False) for _ in range(51)]
    target = targets[-1]
    f.world.execute(
        "UPDATE med.purchase_order SET created_at=now()+interval '1 hour' WHERE id=:id", id=target
    )
    service = Reminders(f.world.uow)
    row = next(
        r
        for r in service.listing(f.maker, f.world.entity, "work", 50)["items"]
        if r["target_id"] == str(target)
    )
    claimed = act(f, row)
    assert case(f, claimed)["item"]["condition_present"]
    with pytest.raises(BusinessError, match="仍触发"):
        act(f, claimed, "CLOSE")
    f.service.transition(
        f.reviewer, target, Transition(expected_version="1", reason="审批测试"), str(uuid4()), True
    )
    assert act(f, case(f, claimed)["item"], "CLOSE")["process_state"] == "CLOSED"
    assert any(
        r["source_key"] == f"work:purchase_receive:{target}"
        for r in service.listing(f.maker, f.world.entity, "work", 50)["items"]
    )


def test_scope_expiry_and_certificate_renewal_close_only_after_parent_review(flow):
    f = flow
    target, _, permit, _ = setup(f, expired=True)
    scopes = inbox(f, "scope")
    assert len(scopes) == 1 and "III类" in scopes[0]["title"]
    assert scopes[0]["scope_codes"] == ["06"] and scopes[0]["channel"] == "PURCHASE"
    assert "到期" in scopes[0]["description"]
    # Long, versioned scope IDs must remain valid command targets and read markers.
    read_body = MessageRead(
        entity_id=f.world.entity, source="scope", message_id=scopes[0]["message_id"]
    )
    read_key = str(uuid4())
    assert Management(f.world.uow).mark_read(f.maker, read_body, read_key)["read"]
    assert Management(f.world.uow).mark_read(f.maker, read_body, read_key)["read"]
    assert inbox(f, "scope")[0]["read"]
    scope_case = act(f, scopes[0])
    cert_row = next(r for r in inbox(f, "certificate") if r["target_id"] == str(target))
    cert_case = act(f, cert_row)
    certs = PartyCertificates(f.world.uow)
    current = certs.detail(f.maker, target, UUID(permit["id"]))["record"]
    certs.save(
        f.maker,
        target,
        revision(current, valid_until=f.day + timedelta(days=300)),
        str(uuid4()),
        UUID(permit["id"]),
    )
    pending = inbox(f, "catalog_review")
    assert len(pending) == 1 and pending[0]["target_id"] == str(target)
    review_case = act(f, pending[0])
    for owned in (scope_case, cert_case):
        with pytest.raises(BusinessError, match="仍触发"):
            act(f, case(f, owned)["item"], "CLOSE")
    approve(f)
    for owned in (scope_case, cert_case, review_case):
        resolved = case(f, owned)["item"]
        assert not resolved["condition_present"]
        assert act(f, resolved, "CLOSE")["process_state"] == "CLOSED"
    assert not inbox(f, "scope")
    assert not inbox(f, "catalog_review")


def test_new_condition_reopens_history_instead_of_being_hidden(flow):
    f = flow
    record = save(f, "maintenance", maintenance(f))
    first = act(f, inbox(f, "maintenance")[0])
    voided = save(f, "maintenance", maintenance(f, state="VOID"), UUID(record["id"]), "1")
    assert voided["version"] == "2"
    closed = act(f, case(f, first)["item"], "CLOSE")
    # Simulate source correction; closed history must not suppress a returned condition.
    f.world.execute(
        "UPDATE med.management_record SET payload=jsonb_set(payload,'{state}','\"COMPLETED\"'),"
        "version=version+1 WHERE id=:id",
        id=UUID(record["id"]),
    )
    returned = inbox(f, "maintenance")[0]
    assert returned["process_state"] == "REOPEN_REQUIRED"
    assert returned["case_id"] == closed["case_id"]
    with pytest.raises(BusinessError, match="已变化"):
        act(f, {**returned, "source_revision": first["source_revision"]})
    reopened = act(f, returned)
    assert reopened["process_state"] == "PROCESSING" and not reopened["closed_at"]
    assert len(case(f, reopened)["history"]) == 3
