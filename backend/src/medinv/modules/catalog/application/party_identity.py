from uuid import UUID, uuid4

from medinv.modules.catalog.api.schemas import PartyCreate
from medinv.modules.catalog.application.master_config import schema_lock
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def resolve_party_identity(repo: SqlSession, ctx: TenantContext, body: PartyCreate) -> UUID | None:
    """Keep legacy integrations compatible; new entry explicitly requests MDM resolution."""
    if body.organization is None:
        return None
    schema_lock(repo, ctx)
    params = {"code": body.credit_code.strip(), "name": body.name.strip()}
    # This is an entity relationship check; never disclose another entity's parties.
    duplicates = repo.rows(
        "SELECT id,code,name FROM med.party WHERE entity_id=:entity AND "
        "(lower(btrim(credit_code))=lower(:code) OR organization_id=:organization)",
        {**params, "entity": body.entity_id, "organization": body.organization.id},
    )
    if duplicates:
        record = duplicates[0]
        raise BusinessError(
            "PARTY_IDENTITY_EXISTS",
            f"当前主体已建档：{record['name']}（{record['code']}）。请使用现有资料；"
            "兼营供销可在修订资料中扩展为供应商及客户。",
            409,
        )
    if body.organization.id:
        candidates = repo.rows(
            "SELECT * FROM med.catalog_organization WHERE id=:id AND kind='ORGANIZATION'",
            {"id": body.organization.id},
        )
        if not candidates:
            raise BusinessError("ORGANIZATION_REQUIRED", "所选机构不可见或不存在", 404)
    else:
        candidates = repo.rows(
            "SELECT * FROM med.catalog_organization WHERE kind='ORGANIZATION' AND "
            "lower(btrim(legal_code))=lower(:code)",
            params,
        )
        if not candidates:
            candidates = repo.rows(
                "SELECT * FROM med.catalog_organization WHERE kind='ORGANIZATION' AND "
                "legal_code='' AND lower(btrim(name))=lower(:name)",
                params,
            )
    if len(candidates) > 1:
        raise BusinessError("ORGANIZATION_AMBIGUOUS", "存在多个相同机构候选，请明确选择", 409)
    if candidates:
        org = candidates[0]
        if org["state"] != "ACTIVE":
            raise BusinessError("ORGANIZATION_INACTIVE", "该机构已停用，请核实资料", 422)
        if org["name"].strip().casefold() != body.name.casefold() or (
            org["legal_code"]
            and org["legal_code"].strip().casefold() != body.credit_code.casefold()
        ):
            raise BusinessError(
                "ORGANIZATION_IDENTITY_CONFLICT",
                "名称或信用代码与机构档案不一致，请重新匹配核实",
                409,
            )
        target = UUID(str(org["id"]))
        if not org["legal_code"]:
            # Never overwrite a known legal identifier, or silently rename an institution.
            if repo.rows(
                "SELECT id FROM med.catalog_organization WHERE kind='ORGANIZATION' "
                "AND lower(btrim(legal_code))=lower(:code) AND id<>:id",
                {**params, "id": target},
            ):
                raise BusinessError("ORGANIZATION_IDENTITY_CONFLICT", "信用代码已属于其他机构", 409)
            repo.execute(
                "UPDATE med.catalog_organization SET legal_code=:code,version=version+1 "
                "WHERE id=:id",
                {**params, "id": target},
            )
            audit(repo, ctx, "organization", target, "master.organization.identified", params)
    else:
        target = uuid4()
        repo.execute(
            "INSERT INTO med.catalog_organization(tenant_id,id,name,legal_code,kind,created_by) "
            "VALUES(:t,:id,:name,:code,'ORGANIZATION',:actor)",
            {**params, "t": ctx.tenant_id, "id": target, "actor": ctx.principal_id},
        )
        audit(repo, ctx, "organization", target, "master.organization.created", params)
    return target
