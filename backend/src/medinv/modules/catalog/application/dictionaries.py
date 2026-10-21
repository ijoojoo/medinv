import json
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.dictionaries import DictionaryKey, DictionaryUpdate
from medinv.modules.catalog.application.dictionary_metadata import (
    GROUPS,
    enrich,
    identity,
    metadata,
    usage,
    validate_metadata,
)
from medinv.modules.catalog.application.dictionary_rules import (
    dictionary_bindings,
    lock_dictionary_bindings,
    reject_dictionary_conflicts,
)
from medinv.platform.database.commands import command
from medinv.platform.database.events import audit
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext

DEFINITIONS = {
    "drug_business_scope": ("药品经营范围类别", 100, "STRICT", []),
    "device_business_scope": ("器械经营范围类别", 100, "STRICT", []),
    "dosage_form": ("剂型", 100, "SUGGEST", ["片剂", "胶囊剂", "颗粒剂", "口服溶液剂", "注射剂"]),
    "trade_name": ("商品名 / 品牌", 100, "SUGGEST", []),
    "storage_conditions": ("储存条件", 500, "SUGGEST", []),
    "base_unit": ("基本单位", 30, "SUGGEST", ["个", "盒", "瓶", "袋", "支", "片", "包"]),
    "recommendation_level": ("推荐级别", 60, "STRICT", ["重点推荐", "常规", "暂不推荐"]),
    "abc_class": ("ABC 分类（两维共用）", 60, "STRICT", ["A", "B", "C"]),
    "purchase_approval": ("采购审核意见", 60, "STRICT", ["同意"]),
    "inspection_qualified": ("验收结论 · 合格", 60, "STRICT", ["合格"]),
    "inspection_mixed": ("验收结论 · 部分拒收", 60, "STRICT", ["部分合格"]),
    "inspection_rejected": ("验收结论 · 全部拒收", 60, "STRICT", ["不合格"]),
    "supplier_grade": ("供应商分级", 60, "STRICT", []),
    "customer_grade": ("客户等级", 60, "STRICT", []),
    "delivery_method": ("配送方式", 60, "STRICT", []),
    "payment_method": ("结算方式", 60, "STRICT", []),
    "department": ("职员部门", 100, "STRICT", []),
    "staff_status": ("职员状态", 30, "STRICT", ["在职", "离职"]),
    "maintenance_type": ("养护类型", 100, "STRICT", []),
}

PURCHASE_DICTIONARIES = (
    "purchase_approval",
    "inspection_qualified",
    "inspection_mixed",
    "inspection_rejected",
)


def dictionary_lock(repo: SqlSession, ctx: TenantContext, *, write: bool = False) -> None:
    function = "pg_advisory_xact_lock" if write else "pg_advisory_xact_lock_shared"
    repo.execute(
        f"SELECT {function}(hashtextextended(:scope,0))",
        {"scope": f"catalog-dictionaries:{ctx.tenant_id}"},
    )


def current_dictionary(repo: SqlSession, key: str) -> dict[str, Any]:
    label, capacity, mode, values = DEFINITIONS[key]
    rows = repo.rows(
        "SELECT version,mode,items FROM med.catalog_dictionary WHERE key=:k", {"k": key}
    )
    row: dict[str, Any] = (
        rows[0]
        if rows
        else {
            "version": 0,
            "mode": mode,
            "items": [
                {"value": v, "active": True, "order": (i + 1) * 10} for i, v in enumerate(values)
            ],
        }
    )
    return {
        **row,
        "key": key,
        **metadata(key),
        "name": label,
        "capacity": capacity,
        "version": str(row["version"]),
        "items": enrich(
            repo, key, sorted(row["items"], key=lambda i: (i["order"], i["value"])), values
        ),
    }


def validate_dictionary(
    repo: SqlSession,
    ctx: TenantContext,
    key: str,
    value: str,
    previous: str | None = None,
    *,
    label: str | None = None,
) -> None:
    if not value or value == previous:
        return
    dictionary_lock(repo, ctx)
    definition = current_dictionary(repo, key)
    item = next((i for i in definition["items"] if i["value"] == value), None)
    if (item and not item["active"]) or (not item and definition["mode"] == "STRICT"):
        raise BusinessError(
            "DICTIONARY_VALUE_INVALID",
            f"{label or definition['name']}请选择当前启用的字典值；已有历史值可保留",
            422,
        )


class CatalogDictionaries:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def list(self, ctx: TenantContext) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            return {"items": [current_dictionary(repo, k) for k in DEFINITIONS]}

    def navigation(self, ctx: TenantContext) -> dict[str, Any]:
        data = self.list(ctx)
        return {
            "groups": [
                {"key": k, "id": identity("group", k), "name": n, "icon": i, "description": d}
                for k, n, i, d in GROUPS
            ],
            "items": [
                {k: v for k, v in d.items() if k != "items"} | {"count": len(d["items"])}
                for d in data["items"]
            ],
        }

    def search(self, ctx: TenantContext, query: str) -> dict[str, Any]:
        query = query.strip().casefold()
        if not query:
            ctx.require("catalog.read")
            return {"items": []}
        matches = []
        groups = {g[0]: g[1] for g in GROUPS}
        for d in self.list(ctx)["items"]:
            path = groups[d["group_key"]] + " / " + d["name"]
            if query in (path + " " + d["key"]).casefold():
                matches.append({"key": d["key"], "path": path, "value": "", "id": ""})
            for item in d["items"]:
                if (
                    query
                    in (item["value"] + " " + item["code"] + " " + item["description"]).casefold()
                ):
                    matches.append(
                        {"key": d["key"], "path": path, "value": item["value"], "id": item["id"]}
                    )
        return {"items": matches[:50], "has_more": len(matches) > 50}

    def detail(self, ctx: TenantContext, key: DictionaryKey) -> dict[str, Any]:
        ctx.require("catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            lock_dictionary_bindings(repo, ctx, key)
            dictionary_lock(repo, ctx)
            definition = current_dictionary(repo, key)
            rows = repo.rows(
                """SELECT a.created_at,a.created_by,a.reason FROM med.audit_event a
                JOIN med.catalog_dictionary d ON (d.tenant_id,d.id)=(a.tenant_id,a.resource_id)
                WHERE a.resource_type='catalog_dictionary' AND d.key=:k
                ORDER BY a.created_at DESC,a.id LIMIT 20""",
                {"k": key},
            )
            return dict(
                jsonable_encoder(
                    {
                        **definition,
                        "usage": usage(repo, key),
                        "bindings": dictionary_bindings(repo, key, definition),
                        "history": [{**r, "detail": json.loads(r["reason"])} for r in rows],
                    }
                )
            )

    def update(
        self, ctx: TenantContext, key: DictionaryKey, body: DictionaryUpdate, command_key: str
    ) -> dict[str, Any]:
        ctx.require("catalog.configure")

        def run(repo: SqlSession) -> dict[str, Any]:
            lock_dictionary_bindings(repo, ctx, key)
            dictionary_lock(repo, ctx, write=True)
            before = current_dictionary(repo, key)
            if body.expected_version != before["version"]:
                raise BusinessError("VERSION_CONFLICT", "字典已变化，请重新加载后修改", 409)
            values = [i.value for i in body.items]
            if key in PURCHASE_DICTIONARIES and (
                body.mode != "STRICT" or not any(i.active for i in body.items)
            ):
                raise BusinessError(
                    "DICTIONARY_INVALID", "采购结论使用严格字典，且至少保留一个启用值", 422
                )
            if len(values) != len(set(values)) or any(len(v) > before["capacity"] for v in values):
                raise BusinessError("DICTIONARY_INVALID", "字典值重复或长度超出绑定字段容量", 422)
            if key == "abc_class" and (set(values) != {"A", "B", "C"} or body.mode != "STRICT"):
                raise BusinessError("DICTIONARY_INVALID", "ABC分类保留A、B、C；可排序或停用", 422)
            if (
                key in {"recommendation_level", "drug_business_scope", "device_business_scope"}
                and body.mode != "STRICT"
            ):
                raise BusinessError(
                    "DICTIONARY_INVALID", "推荐级别与经营范围类别使用统一字典值", 422
                )
            if not {i["value"] for i in before["items"]}.issubset(values):
                raise BusinessError(
                    "DICTIONARY_HISTORY_REQUIRED", "已保存的字典值请停用，不可删除或改写", 422
                )
            previous = {i["value"]: i for i in before["items"]}
            raw = []
            for i in body.items:
                item = {
                    **previous.get(i.value, {}),
                    **i.model_dump(mode="json", exclude_unset=True),
                }
                item.setdefault("active", True)
                raw.append(item)
            after = enrich(
                repo, key, sorted(raw, key=lambda i: (i["order"], i["value"])), DEFINITIONS[key][3]
            )
            validate_metadata(key, before["items"], after)
            if before["items"] == after and before["mode"] == body.mode:
                raise BusinessError("DICTIONARY_UNCHANGED", "字典没有变化", 422)
            reject_dictionary_conflicts(
                dictionary_bindings(repo, key, {**before, "mode": body.mode, "items": after})
            )
            row = repo.rows(
                """INSERT INTO med.catalog_dictionary
                (tenant_id,id,key,mode,items,created_by,updated_by)
                VALUES (:t,:id,:k,:mode,CAST(:items AS jsonb),:actor,:actor)
                ON CONFLICT(tenant_id,key) DO UPDATE SET mode=EXCLUDED.mode,items=EXCLUDED.items,
                version=catalog_dictionary.version+1,updated_by=:actor,updated_at=clock_timestamp()
                RETURNING id,version""",
                {
                    "t": ctx.tenant_id,
                    "id": uuid4(),
                    "k": key,
                    "mode": body.mode,
                    "items": json.dumps(after, ensure_ascii=False),
                    "actor": ctx.principal_id,
                },
            )[0]
            audit(
                repo,
                ctx,
                "catalog_dictionary",
                row["id"],
                "catalog.dictionary.updated",
                {
                    "key": key,
                    "before": before,
                    "after": {"mode": body.mode, "items": after},
                    "reason": body.reason,
                    "version": str(row["version"]),
                },
            )
            return {"key": key, "version": str(row["version"])}

        return command(
            self.uow,
            ctx,
            "catalog.dictionary.update",
            key,
            command_key,
            body.model_dump(mode="json"),
            run,
        )
