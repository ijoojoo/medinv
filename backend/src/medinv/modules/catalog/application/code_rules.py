import json
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.code_rules import CodeCategory, ProductCodeRuleUpdate
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def code_lock(repo: SqlSession, ctx: TenantContext) -> None:
    repo.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))",
        {"scope": f"product-codes:{ctx.tenant_id}"},
    )


def current_rule(repo: SqlSession, category: str) -> dict[str, Any]:
    rows = repo.rows("SELECT * FROM med.product_code_rule WHERE category=:c", {"c": category})
    row = (
        rows[0]
        if rows
        else dict(
            category=category,
            enabled=True,
            prefix={
                "DRUG": "YP",
                "HERB": "ZY",
                "DEVICE": "QX",
                "FOOD": "SP",
                "HEALTH": "BJ",
                "FSMP": "TY",
                "INFANT": "YE",
                "COSMETIC": "HZ",
                "DAILY": "RY",
                "SERVICE": "FW",
            }[category],
            date_format="NONE",
            digits=6,
            next_number=1,
            version=0,
        )
    )
    return {
        k: str(row[k]) if k == "version" else row[k]
        for k in (
            "category",
            "enabled",
            "prefix",
            "date_format",
            "digits",
            "next_number",
            "version",
        )
    }


def format_code(rule: dict[str, Any], date: str, number: int) -> str:
    length = {"NONE": 0, "YYYY": 4, "YYYYMM": 6, "YYYYMMDD": 8}[rule["date_format"]]
    return str(rule["prefix"]) + date[:length] + str(number).zfill(rule["digits"])


def local_date(repo: SqlSession) -> str:
    return str(
        repo.rows(
            "SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYYMMDD') AS date", {}
        )[0]["date"]
    )


def resolve_product_codes(
    repo: SqlSession, ctx: TenantContext, category: str, code: str | None, sku_code: str | None
) -> tuple[str, str]:
    code_lock(repo, ctx)
    if code:
        return code, sku_code or code
    rule = current_rule(repo, category)
    if not rule["enabled"]:
        raise BusinessError("CODE_REQUIRED", "当前类别未启用自动编码，请填写商品编码", 422)
    today = local_date(repo)
    for number in range(rule["next_number"], min(rule["next_number"] + 1000, 1000000000000)):
        candidate = format_code(rule, today, number)
        if not repo.rows(
            "SELECT code FROM med.product WHERE code=:code UNION ALL "
            "SELECT code FROM med.sku WHERE code=:code LIMIT 1",
            {"code": candidate},
        ):
            repo.execute(
                """INSERT INTO med.product_code_rule
              (tenant_id,id,category,enabled,prefix,date_format,digits,next_number,created_by,updated_by)
              VALUES (:t,:id,:category,:enabled,:prefix,:date_format,:digits,:next,:actor,:actor)
              ON CONFLICT(tenant_id,category) DO UPDATE SET next_number=:next,
              updated_by=:actor,updated_at=clock_timestamp()""",
                {
                    **rule,
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "next": number + 1,
                    "actor": ctx.principal_id,
                },
            )
            return candidate, candidate
    raise BusinessError(
        "CODE_SEQUENCE_EXHAUSTED", "流水号已用尽或连续冲突，请调整编码前缀或起始号", 409
    )


class ProductCodeRules:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext, category: CodeCategory) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            rule = current_rule(repo, category)
            history = repo.rows(
                """SELECT a.created_at,a.reason FROM med.audit_event a
              JOIN med.product_code_rule r ON (r.tenant_id,r.id)=(a.tenant_id,a.resource_id)
              WHERE a.resource_type='product_code_rule' AND r.category=:c
              ORDER BY a.created_at DESC,a.id LIMIT 10""",
                {"c": category},
            )
            return dict(
                jsonable_encoder(
                    {
                        **rule,
                        "example": format_code(rule, local_date(repo), rule["next_number"]),
                        "history": [
                            {"created_at": h["created_at"], "detail": json.loads(h["reason"])}
                            for h in history
                        ],
                    }
                )
            )

    def update(
        self, ctx: TenantContext, category: CodeCategory, body: ProductCodeRuleUpdate, key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.configure")

        def run(repo: SqlSession) -> dict[str, Any]:
            code_lock(repo, ctx)
            before = current_rule(repo, category)
            if before["version"] != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "编码规则已变化，请重新加载", 409)
            if body.start_number < before["next_number"]:
                raise BusinessError(
                    "CODE_SEQUENCE_BACKWARD", "起始号不能小于当前流水号，请重新加载后设置", 422
                )
            after = body.model_dump(exclude={"expected_version", "reason", "start_number"})
            after["next_number"] = body.start_number
            if all(before[k] == v for k, v in after.items()):
                raise BusinessError("CODE_RULE_UNCHANGED", "编码规则没有变化", 422)
            result = repo.rows(
                """INSERT INTO med.product_code_rule
              (tenant_id,id,category,enabled,prefix,date_format,digits,next_number,created_by,updated_by)
              VALUES(:t,:id,:category,:enabled,:prefix,:date_format,:digits,:next_number,:actor,:actor)
              ON CONFLICT(tenant_id,category) DO UPDATE SET enabled=:enabled,prefix=:prefix,
              date_format=:date_format,digits=:digits,next_number=:next_number,
              version=product_code_rule.version+1,updated_by=:actor,updated_at=clock_timestamp()
              RETURNING id,version""",
                {
                    **after,
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "category": category,
                    "actor": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "product_code_rule",
                result["id"],
                "catalog.code_rule.updated",
                {
                    "before": before,
                    "after": after,
                    "reason": body.reason,
                    "version": str(result["version"]),
                },
            )
            return {"category": category, "version": str(result["version"])}

        return command(
            self.uow,
            ctx,
            "catalog.code_rule.update",
            category,
            key,
            body.model_dump(mode="json"),
            run,
        )
