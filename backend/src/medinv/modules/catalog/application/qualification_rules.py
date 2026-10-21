"""Editable, versioned defaults. A source citation is provenance, never a lock."""

import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.qualification import QualificationSettings, RulesSave
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

DEVICE_SOURCE = "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_51afc62ef3c84455b28b113a628f9e35.html"
DRUG_SOURCE = "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_db526cfcd7204874b8b23297fa3b02dc.html"


def defaults() -> dict[str, Any]:
    rules = []
    for category in ("DEVICE", "DRUG"):
        source = DEVICE_SOURCE if category == "DEVICE" else DRUG_SOURCE
        title = (
            "医疗器械经营监督管理办法" if category == "DEVICE" else "药品经营和使用质量监督管理办法"
        )
        common = {
            "category": category,
            "source_title": title,
            "source_url": source,
            "effective_from": "2022-05-01" if category == "DEVICE" else "2024-01-01",
            "checked_on": "2026-10-21",
            "channels": ["PURCHASE", "WHOLESALE"],
            "subjects": ["DISTRIBUTOR"],
        }
        rules.append(
            {
                **common,
                "id": category.lower() + "-identity",
                "name": "经营企业主体资料",
                "article": "经营主体资质核对（企业资料模板）",
                "certificate_groups": [["BUSINESS_LICENSE"]],
                "note": "营业执照只证明主体身份，不单独证明全部经营资格。",
            }
        )
        if category == "DEVICE":
            for level, certificate, name in [
                ("II", "DEVICE_BUSINESS_FILING", "第二类器械经营备案"),
                ("III", "DEVICE_BUSINESS_LICENSE", "第三类器械经营许可"),
            ]:
                rules.append(
                    {
                        **common,
                        "id": "device-" + level.lower(),
                        "name": name,
                        "device_classes": [level],
                        "article": "第四条、第二十五条" if level == "II" else "第四条、第十四条",
                        "certificate_groups": [[certificate]],
                        "note": "免备案等情形须在具体经营范围中记录适用依据并随主体审核。",
                    }
                )
        else:
            rules.append(
                {
                    **common,
                    "id": "drug-distributor",
                    "name": "药品经营企业许可",
                    "article": "第三条、第十一条、第三章：许可与经营质量管理",
                    "certificate_groups": [["DRUG_BUSINESS_LICENSE"]],
                }
            )
        rules.append(
            {
                **common,
                "id": category.lower() + "-registrant",
                "name": "注册人 / 持有人自行销售依据",
                "subjects": ["REGISTRANT"],
                "article": "第三条、第二十七条" if category == "DEVICE" else "第三条",
                "certificate_groups": [["BUSINESS_LICENSE"], ["OTHER"]],
                "note": (
                    "其他证明须核对主体身份、产品归属、销售场所及免经营许可的适用条件；"
                    "不可泛化为代理销售资格。"
                ),
            }
        )
        rules.append(
            {
                **common,
                "id": category.lower() + "-institution",
                "name": "医疗机构客户资质",
                "subjects": ["MEDICAL_INSTITUTION"],
                "channels": ["WHOLESALE"],
                "article": "购货单位 / 使用单位资质核查（企业资料模板）",
                "certificate_groups": [["MEDICAL_INSTITUTION_LICENSE"]],
                "note": "医疗机构购入使用与经营企业转售分别核查；特殊使用单位可另配规则。",
            }
        )
    return QualificationSettings.model_validate({"rules": rules}).model_dump(mode="json")


def current(repo: SqlSession) -> dict[str, Any]:
    repo.execute(
        "SELECT pg_advisory_xact_lock_shared(hashtextextended("
        "'qualification-rules:' || current_setting('app.tenant_id'),0))",
        {},
    )
    rows = repo.rows("SELECT * FROM med.qualification_rules", {})
    if rows:
        return {
            **rows[0],
            "payload": QualificationSettings.model_validate(rows[0]["payload"]).model_dump(
                mode="json"
            ),
        }
    return {"id": None, "version": 0, "payload": defaults()}


def settings(repo: SqlSession) -> dict[str, Any]:
    return dict(current(repo)["payload"])


class QualificationRules:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = current(repo)
            history = (
                repo.rows(
                    "SELECT created_at,created_by,reason FROM med.audit_event "
                    "WHERE resource_type='qualification_rules' "
                    "ORDER BY created_at DESC,id LIMIT 30",
                    {},
                )
                if "catalog.configure" in ctx.permissions
                else []
            )
            return dict(
                jsonable_encoder(
                    {
                        **row,
                        "version": str(row["version"]),
                        "defaults": defaults(),
                        "history": [{**r, "detail": json.loads(r["reason"])} for r in history],
                    }
                )
            )

    def save(self, ctx: TenantContext, body: RulesSave, key: str) -> dict[str, Any]:
        ctx.require("catalog.configure")

        def run(repo: SqlSession) -> dict[str, Any]:
            repo.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
                {"key": f"qualification-rules:{ctx.tenant_id}"},
            )
            before = current(repo)
            if str(before["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "规则已变化，请刷新后修改", 409)
            payload = body.payload.model_dump(mode="json")
            row = repo.rows(
                "INSERT INTO med.qualification_rules(tenant_id,id,payload,updated_by) "
                "VALUES(:t,:id,CAST(:p AS jsonb),:a) ON CONFLICT(tenant_id) DO UPDATE SET "
                "payload=EXCLUDED.payload,version=qualification_rules.version+1,"
                "updated_by=:a,updated_at=clock_timestamp() RETURNING *",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "p": json.dumps(payload),
                    "a": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "qualification_rules",
                row["id"],
                "catalog.qualification_rules_saved",
                {
                    "before": deepcopy(before["payload"]),
                    "after": payload,
                    "reason": body.reason,
                    "version": str(row["version"]),
                },
            )
            return dict(jsonable_encoder({**row, "version": str(row["version"])}))

        return command(
            self.uow,
            ctx,
            "catalog.qualification_rules.save",
            "tenant",
            key,
            body.model_dump(mode="json"),
            run,
        )
