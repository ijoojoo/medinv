from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_management_workspaces import save
from test_packaging import receive_and_post
from test_procurement import flow as flow
from test_purchase_close import receipt_body
from test_purchase_revisions import body as revision_body
from test_purchase_revisions import revise

from medinv.modules.procurement.api.schemas import Transition
from medinv.modules.workspace.approvals import ApprovalWorkflows, PolicyPublish
from medinv.modules.workspace.management import Management
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.commit_guard import before_business_commit
from medinv.platform.errors import BusinessError

pytestmark = pytest.mark.integration


def design(f, roles=("QUALITY", "ADMIN"), business="PURCHASE", mode="ANY", record=None):
    return save(
        f,
        "approval_design",
        {
            "name": "软件验收顺序审批",
            "business": business,
            "steps": [
                {"id": str(uuid4()), "name": f"节点{i + 1}", "role": role, "mode": mode}
                for i, role in enumerate(roles)
            ],
        },
        UUID(record["id"]) if record else None,
        record["version"] if record else "0",
    )


def publish(f, record, *, enabled=True, ctx=None, key=None, version=None):
    svc = ApprovalWorkflows(f.world.uow)
    current = svc.get(f.maker, f.world.entity)
    return svc.publish(
        ctx or f.maker,
        PolicyPublish(
            entity_id=f.world.entity,
            expected_version=version or current["version"],
            design_id=record["id"],
            design_version=record["version"],
            enabled=enabled,
            reason="隔离企业发布测试",
        ),
        key or str(uuid4()),
    )


def actor(f, role):
    return replace(
        f.reviewer,
        principal_id=uuid4(),
        permissions=ROLE_PERMISSIONS[role],
        role_codes=frozenset({role}),
    )


def doc(f, target, ctx=None):
    return f.service.detail(ctx or f.maker, target)


def approve(f, target, ctx, version=None, key=None):
    return f.service.transition(
        ctx,
        target,
        Transition(
            expected_version=version or doc(f, target)["order"]["version"], reason="核对本节点事项"
        ),
        key or str(uuid4()),
        True,
    )


def transition(f, target, action, ctx, version=None, key=None):
    return ApprovalWorkflows(f.world.uow).transition(
        ctx,
        target,
        Transition(
            expected_version=version or doc(f, target)["order"]["version"],
            reason="核对退回或重提说明",
        ),
        key or str(uuid4()),
        action,
    )


def test_publish_binds_new_orders_and_sequential_final_approval_allows_stock(flow):
    f = flow
    old = f.order(approve=False)
    config = design(f)
    key = str(uuid4())
    first = publish(f, config, version="0", key=key)
    assert publish(f, config, version="0", key=key) == first
    assert doc(f, old)["approval_workflow"] is None
    approve(f, old, f.reviewer)
    target = f.order(approve=False)
    quality, admin = actor(f, "QUALITY"), actor(f, "ADMIN")
    workflow = doc(f, target, quality)["approval_workflow"]
    assert workflow["policy_version"] == "1" and workflow["can_approve"]
    with pytest.raises(BusinessError, match="指定角色"):
        approve(f, target, admin)
    with pytest.raises(BusinessError, match="不能审核"):
        approve(f, target, replace(f.maker, role_codes=frozenset({"QUALITY"})))
    assert approve(f, target, quality)["state"] == "DRAFT"
    interim = doc(f, target)
    assert interim["order"]["approved_by"] is None
    assert interim["approval_workflow"]["step_index"] == 1
    with pytest.raises(BusinessError):
        f.service.receive(f.maker, target, receipt_body(f, target, "1"), str(uuid4()))
    assert f.world.scalar("SELECT count(*) FROM med.posting_batch") == 0
    assert approve(f, target, admin)["state"] == "APPROVED"
    assert doc(f, target)["approval_workflow"]["state"] == "APPROVED"
    receive_and_post(f, target, "10")
    assert f.world.scalar("SELECT count(*) FROM med.payable_obligation") == 1


def test_published_snapshot_ignores_draft_edit_republish_and_disable_for_bound_orders(flow):
    f = flow
    first = design(f)
    publish(f, first)
    bound = f.order(approve=False)
    second = design(f, roles=("OWNER",), record=first)
    assert ApprovalWorkflows(f.world.uow).get(f.maker, f.world.entity)["design_version"] == "1"
    publish(f, second)
    newer = f.order(approve=False)
    assert len(doc(f, bound)["approval_workflow"]["definition"]["steps"]) == 2
    assert doc(f, newer)["approval_workflow"]["definition"]["steps"][0]["role"] == "OWNER"
    publish(f, second, enabled=False)
    assert doc(f, f.order(approve=False))["approval_workflow"] is None
    assert approve(f, bound, actor(f, "QUALITY"))["state"] == "DRAFT"
    assert approve(f, bound, actor(f, "ADMIN"))["state"] == "APPROVED"
    assert approve(f, newer, actor(f, "OWNER"))["state"] == "APPROVED"


def test_return_resubmit_preserves_snapshot_restarts_steps_and_records_prior_round(flow):
    f = flow
    record = design(f)
    publish(f, record)
    target = f.order(approve=False)
    quality, admin = actor(f, "QUALITY"), actor(f, "ADMIN")
    approve(f, target, quality)
    transition(f, target, "reject", admin)
    assert doc(f, target)["approval_workflow"]["state"] == "RETURNED"
    with pytest.raises(BusinessError):
        approve(f, target, admin)
    publish(f, record, enabled=False)
    transition(f, target, "resubmit", f.maker)
    resumed = doc(f, target)["approval_workflow"]
    assert resumed["round"] == 2 and resumed["step_index"] == 0
    assert resumed["policy_version"] == "1"
    assert [d["action"] for d in resumed["decisions"]] == ["APPROVE", "REJECT", "RESUBMIT"]
    assert resumed["decisions"][-1]["role"] is None
    with pytest.raises(BusinessError, match="指定角色"):
        approve(f, target, admin)
    approve(f, target, quality)
    approve(f, target, admin)
    assert len(doc(f, target)["approval_workflow"]["decisions"]) == 5


def test_amendment_rebinds_current_policy_and_preserves_old_approval_evidence(flow):
    f = flow
    record = design(f)
    publish(f, record)
    target = f.order(approve=False)
    approve(f, target, actor(f, "QUALITY"))
    updated = design(f, roles=("ADMIN",), record=record)
    publish(f, updated)
    revise(f, revision_body(f, target), oid=target)
    workflow = doc(f, target)["approval_workflow"]
    assert workflow["revision_no"] == 2 and workflow["policy_version"] == "2"
    assert workflow["previous"][0]["state"] == "SUPERSEDED"
    assert len(workflow["previous"][0]["decisions"]) == 1
    approve(f, target, actor(f, "ADMIN"))
    assert doc(f, target)["order"]["approval_state"] == "APPROVED"


def test_concurrent_votes_do_not_skip_node_idempotent_retry_and_permission_guard(flow):
    f = flow
    publish(f, design(f, roles=("QUALITY", "QUALITY")))
    target = f.order(approve=False)
    quality = actor(f, "QUALITY")

    def sign(_):
        try:
            approve(f, target, quality, version="1")
            return "ok"
        except BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(sign, range(2))) == ["VERSION_CONFLICT", "ok"]
    key = str(uuid4())
    result = approve(f, target, quality, version="2", key=key)
    assert approve(f, target, quality, version="2", key=key) == result
    assert len(doc(f, target)["approval_workflow"]["decisions"]) == 2
    with pytest.raises(BusinessError):
        approve(f, target, replace(quality, entity_ids=frozenset()), version="2", key=key)


def test_cancel_and_rollback_are_atomic_with_original_order(flow):
    f = flow
    publish(f, design(f))
    target = f.order(approve=False)
    quality = actor(f, "QUALITY")

    def reject():
        raise BusinessError("REVOKED", "提交前权限变化", 403)

    token = before_business_commit.set(reject)
    try:
        with pytest.raises(BusinessError, match="权限变化"):
            approve(f, target, quality)
    finally:
        before_business_commit.reset(token)
    assert doc(f, target)["order"]["version"] == "1"
    assert not doc(f, target)["approval_workflow"]["decisions"]
    f.service.transition(
        f.maker, target, Transition(expected_version="1", reason="取消测试"), str(uuid4()), False
    )
    assert doc(f, target)["approval_workflow"]["state"] == "CANCELLED"


def test_publish_permissions_versions_modes_and_single_master_review(flow):
    f = flow
    record = design(f)
    with pytest.raises(BusinessError):
        publish(f, record, ctx=actor(f, "QUALITY"))
    publish(f, record)
    with pytest.raises(BusinessError, match="已变化"):
        publish(f, record, version="0")
    with pytest.raises(BusinessError, match="会签"):
        publish(f, design(f, mode="ALL"))
    with pytest.raises(BusinessError, match="本期支持采购"):
        publish(f, design(f, business="SALES"))
    for business in ("PRODUCT", "PARTY"):
        with pytest.raises(BusinessError, match="一次审核"):
            design(f, business=business)
        valid = design(f, roles=("QUALITY",), business=business)
        assert Management(f.world.uow).detail(
            f.maker, "approval_design", f.world.entity, UUID(valid["id"])
        )


def test_role_permission_revocation_and_replay_cannot_bypass_current_entity_scope(flow):
    f = flow
    publish(f, design(f, roles=("QUALITY",)))
    target = f.order(approve=False)
    quality = actor(f, "QUALITY")
    # Matching broad permissions without the actual role do not satisfy a configured role node.
    with pytest.raises(BusinessError, match="指定角色"):
        approve(f, target, replace(quality, role_codes=frozenset()))
    key = str(uuid4())
    transition(f, target, "reject", quality, version="1", key=key)
    with pytest.raises(BusinessError):
        transition(
            f, target, "reject", replace(quality, entity_ids=frozenset()), version="1", key=key
        )
    with pytest.raises(BusinessError):
        transition(f, target, "resubmit", actor(f, "VIEWER"))
