"""Resumable operator provisioning. No public HTTP entry point or implicit signup."""

import hashlib
import json
from typing import Annotated, Any, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Connection, Engine, text

from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

Name = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
]
Code = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=50)
]


class InitialMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: UUID
    role_codes: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def roles(self) -> Self:
        if any(code not in ROLE_PERMISSIONS for code in self.role_codes):
            raise ValueError("unknown role")
        self.role_codes = sorted(set(self.role_codes))
        return self


class EnterprisePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    tenant_name: Name
    entity_name: Name
    credit_code: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=32)]
    warehouse_code: Code
    warehouse_name: Name
    location_code: Code
    reason: Name
    members: list[InitialMember] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def membership(self) -> Self:
        if len({m.principal_id for m in self.members}) != len(self.members):
            raise ValueError("duplicate member")
        owners = [m for m in self.members if "OWNER" in m.role_codes]
        if len(owners) != 1:
            raise ValueError("exactly one initial owner required")
        if not any(
            m.principal_id != owners[0].principal_id
            and "opening.review" in set().union(*(ROLE_PERMISSIONS[c] for c in m.role_codes))
            for m in self.members
        ):
            raise ValueError("an independent reviewer is required")
        self.members.sort(key=lambda m: str(m.principal_id))
        return self

    @property
    def owner(self) -> UUID:
        return next(m.principal_id for m in self.members if "OWNER" in m.role_codes)

    def ids(self) -> dict[str, UUID]:
        return {
            key: uuid5(self.request_id, key)
            for key in ("tenant", "entity", "warehouse", "location")
        }


class EnterpriseProvisioner:
    def __init__(self, control: Engine, business: TenantUnitOfWork):
        self.control, self.business = control, business

    def run(self, plan: EnterprisePlan) -> dict[str, str]:
        ids = plan.ids()
        payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with self.control.connect() as conn:
            try:
                # Session lock spans the two committed control phases and business transaction.
                # Closing the dedicated connection below releases it on every exit path.
                conn.execute(text("SET ROLE control_owner"))
                conn.execute(text("SET lock_timeout='5s'"))
                conn.execute(
                    text("SELECT pg_advisory_lock(hashtextextended(:key,0))"),
                    {"key": "enterprise-provision/" + str(plan.request_id)},
                )
                conn.commit()
                with conn.begin():
                    complete = self.prepare(conn, plan, payload, fingerprint)
                if not complete:
                    self.initialize_business(plan)
                    with conn.begin():
                        self.activate(conn, plan)
                return {**{k + "_id": str(v) for k, v in ids.items()}, "state": "COMPLETED"}
            finally:
                # Do not return operator role/session advisory locks to a shared connection pool.
                conn.invalidate()

    def prepare(self, conn: Connection, plan: EnterprisePlan, payload: str, digest: str) -> bool:
        ids = plan.ids()
        prior = (
            conn.execute(
                text("SELECT * FROM control.enterprise_provisioning WHERE request_id=:id"),
                {"id": plan.request_id},
            )
            .mappings()
            .one_or_none()
        )
        if prior:
            if prior["request_hash"] != digest:
                raise BusinessError(
                    "PROVISION_CONFLICT", "开通编号已绑定另一份资料，请核对原清单", 409
                )
            if prior["state"] == "COMPLETED":
                return True  # Never re-enable tenants or overwrite later member/warehouse edits.
            self.pending(conn, plan)
            return False
        members = (
            conn.execute(
                text("SELECT id FROM control.principal WHERE id=ANY(:ids)"),
                {"ids": [m.principal_id for m in plan.members]},
            )
            .scalars()
            .all()
        )
        if len(members) != len(plan.members):
            raise BusinessError("MEMBER_NOT_FOUND", "首批成员尚未建立身份，请先核对账号", 404)
        conn.execute(
            text("INSERT INTO control.tenant(id,name,active) VALUES(:tenant,:name,false)"),
            {**ids, "name": plan.tenant_name},
        )
        conn.execute(
            text("INSERT INTO control.entity(tenant_id,id,name) VALUES(:tenant,:entity,:name)"),
            {**ids, "name": plan.entity_name},
        )
        for member in plan.members:
            permissions = sorted(set().union(*(ROLE_PERMISSIONS[c] for c in member.role_codes)))
            conn.execute(
                text("""INSERT INTO control.membership
                (tenant_id,principal_id,entity_ids,permissions,role_codes,active)
                VALUES(:tenant,:principal,:entities,:permissions,:roles,false)"""),
                {
                    **ids,
                    "principal": member.principal_id,
                    "entities": [ids["entity"]],
                    "permissions": permissions,
                    "roles": member.role_codes,
                },
            )
        conn.execute(
            text("""INSERT INTO control.enterprise_provisioning
            (request_id,tenant_id,request_hash,plan)
            VALUES(:id,:tenant,:hash,CAST(:plan AS jsonb))"""),
            {**ids, "id": plan.request_id, "hash": digest, "plan": payload},
        )
        self.control_audit(conn, plan, "enterprise.provision_requested")
        return False

    @staticmethod
    def pending(conn: Connection, plan: EnterprisePlan) -> None:
        ids = plan.ids()
        tid = ids["tenant"]
        tenant = (
            conn.execute(text("SELECT active,name FROM control.tenant WHERE id=:id"), {"id": tid})
            .mappings()
            .one()
        )
        members = (
            conn.execute(
                text("""SELECT principal_id,active,role_codes,entity_ids,permissions,
                    permission_version FROM control.membership WHERE tenant_id=:id"""),
                {"id": tid},
            )
            .mappings()
            .all()
        )
        entities = (
            conn.execute(
                text("SELECT id,name FROM control.entity WHERE tenant_id=:id"), {"id": tid}
            )
            .mappings()
            .all()
        )
        expected = {m.principal_id: m for m in plan.members}
        invalid = tenant["active"] or tenant["name"] != plan.tenant_name
        invalid |= [dict(row) for row in entities] != [
            {"id": ids["entity"], "name": plan.entity_name}
        ]
        invalid |= {m["principal_id"] for m in members} != set(expected)
        for member in members:
            target = expected.get(member["principal_id"])
            if target is None:
                continue
            permissions = sorted(set().union(*(ROLE_PERMISSIONS[c] for c in target.role_codes)))
            invalid |= bool(
                member["active"]
                or member["permission_version"] != 1
                or member["role_codes"] != target.role_codes
                or member["entity_ids"] != [ids["entity"]]
                or member["permissions"] != permissions
            )
        if invalid:
            raise BusinessError(
                "PROVISION_INCONSISTENT", "未完成开通的企业或成员已被改动，请由平台核查", 409
            )

    def initialize_business(self, plan: EnterprisePlan) -> None:
        ids = plan.ids()
        ctx = TenantContext(
            ids["tenant"],
            plan.owner,
            plan.request_id,
            1,
            1,
            frozenset({ids["entity"]}),
            frozenset(),
        )
        with self.business.open(ctx) as session:
            repo = SqlSession(session)
            existing = repo.rows("SELECT id,name,credit_code,status FROM med.legal_entity", {})
            if existing:
                # Pending tenants are unreachable by web sessions. Only this exact bootstrap
                # snapshot may be resumed; unknown existing business data is never adopted.
                warehouse = repo.rows("SELECT id,entity_id,code,name,kind FROM med.warehouse", {})
                location = repo.rows("SELECT id,warehouse_id,code FROM med.location", {})
                marker = repo.rows(
                    """SELECT id FROM med.audit_event WHERE request_id=:id
                    AND action='enterprise.provisioned' AND resource_id=:entity""",
                    {"id": plan.request_id, **ids},
                )
                if (
                    existing
                    != [
                        {
                            "id": ids["entity"],
                            "name": plan.entity_name,
                            "credit_code": plan.credit_code,
                            "status": "ACTIVE",
                        }
                    ]
                    or warehouse
                    != [
                        {
                            "id": ids["warehouse"],
                            "entity_id": ids["entity"],
                            "code": plan.warehouse_code,
                            "name": plan.warehouse_name,
                            "kind": "PHYSICAL",
                        }
                    ]
                    or location
                    != [
                        {
                            "id": ids["location"],
                            "warehouse_id": ids["warehouse"],
                            "code": plan.location_code,
                        }
                    ]
                    or len(marker) != 1
                ):
                    raise BusinessError(
                        "PROVISION_INCONSISTENT", "业务库开通记录不一致，未启用企业", 409
                    )
                return
            values: dict[str, Any] = {**ids, "actor": plan.owner}
            repo.execute(
                """INSERT INTO med.legal_entity
                (tenant_id,id,created_by,updated_by,name,credit_code,status)
                VALUES(:tenant,:entity,:actor,:actor,:name,:credit,'ACTIVE')""",
                {**values, "name": plan.entity_name, "credit": plan.credit_code},
            )
            repo.execute(
                """INSERT INTO med.warehouse
                (tenant_id,id,created_by,updated_by,entity_id,code,kind,name)
                VALUES(:tenant,:warehouse,:actor,:actor,:entity,:code,'PHYSICAL',:name)""",
                {**values, "code": plan.warehouse_code, "name": plan.warehouse_name},
            )
            repo.execute(
                """INSERT INTO med.location(tenant_id,id,created_by,updated_by,warehouse_id,code)
                VALUES(:tenant,:location,:actor,:actor,:warehouse,:code)""",
                {**values, "code": plan.location_code},
            )
            audit(
                repo,
                ctx,
                "legal_entity",
                ids["entity"],
                "enterprise.provisioned",
                {
                    "reason": plan.reason,
                    "warehouse_id": str(ids["warehouse"]),
                    "source": "platform_operator",
                },
            )

    def activate(self, conn: Connection, plan: EnterprisePlan) -> None:
        self.pending(conn, plan)
        ids = plan.ids()
        conn.execute(text("UPDATE control.membership SET active=true WHERE tenant_id=:tenant"), ids)
        conn.execute(text("UPDATE control.tenant SET active=true WHERE id=:tenant"), ids)
        conn.execute(
            text("""UPDATE control.enterprise_provisioning SET state='COMPLETED',
            completed_at=clock_timestamp() WHERE request_id=:id AND state='PENDING'"""),
            {"id": plan.request_id},
        )
        self.control_audit(conn, plan, "enterprise.provision_completed")

    @staticmethod
    def control_audit(conn: Connection, plan: EnterprisePlan, action: str) -> None:
        ids = plan.ids()
        conn.execute(
            text("""INSERT INTO control.audit_event(id,tenant_id,actor_id,action,target_id,detail)
            VALUES(:id,:tenant,:owner,:action,:tenant,CAST(:detail AS jsonb))"""),
            {
                **ids,
                "id": uuid5(plan.request_id, action),
                "owner": plan.owner,
                "action": action,
                "detail": json.dumps(
                    {
                        "request_id": str(plan.request_id),
                        "reason": plan.reason,
                        "source": "platform_operator",
                        "initial_owner": str(plan.owner),
                    }
                ),
            },
        )
