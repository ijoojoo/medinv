"""Evaluate reviewed party scopes per product; no expiry-driven master suspension."""

from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.api.qualification import ScopeProfile, ScopeSave
from medinv.modules.catalog.application.change_bundle import (
    certificate_rows,
    editable,
    pending,
    stage,
)
from medinv.modules.catalog.application.qualification_rules import current as current_rules
from medinv.platform.database.commands import command
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def profile(repo: SqlSession, party: UUID) -> dict[str, Any] | None:
    rows = repo.rows("SELECT * FROM med.party_scope_profile WHERE party_id=:p", {"p": party})
    return rows[0] if rows else None


def uses_legacy_dates(repo: SqlSession, party: UUID) -> bool:
    return profile(repo, party) is None and bool(
        current_rules(repo)["payload"]["legacy_party_validity"]
    )


def code_matches(granted: str, actual: str, children: bool) -> bool:
    # Delimiter-aware: 06 must never cover 060 or a different directory version.
    return granted == actual or (children and actual.startswith(granted + "-"))


def scope_matches(scope: dict[str, Any], product: dict[str, Any]) -> bool:
    if scope["category"] != product["category"]:
        return False
    if scope["category"] == "DRUG":
        return scope.get("name", scope.get("value")) == product.get("drug_business_scope")
    if not scope.get("codes"):
        return scope.get("value") == product.get("device_business_scope")
    code = product.get("device_classification_code", "")
    return bool(
        code
        and scope.get("device_class") == product.get("device_class")
        and scope.get("directory_version", "2017") == product.get("device_classification_version")
        and any(code_matches(c, code, scope.get("include_children", True)) for c in scope["codes"])
        and not any(code_matches(c, code, True) for c in scope.get("excluded_codes", []))
    )


def applicable_rules(
    config: dict[str, Any], scope: dict[str, Any], channel: str, day: str
) -> list[dict[str, Any]]:
    return [
        r
        for r in config["rules"]
        if r["enabled"]
        and r["category"] == scope["category"]
        and scope["subject_type"] in r["subjects"]
        and channel in r["channels"]
        and (not r["device_classes"] or scope.get("device_class") in r["device_classes"])
        and r["effective_from"] <= day
        and (not r.get("effective_until") or day <= r["effective_until"])
    ]


def certificate_issue(
    cert: dict[str, Any], channel: str, day: str, *, preview: bool = False
) -> str:
    if cert["state"] != "VERIFIED" and not (preview and cert["state"] == "DRAFT"):
        return "未随主体审核或已停用"
    if ("SUPPLY" if channel == "PURCHASE" else "PURCHASE") not in cert["business_uses"]:
        return "证件适用业务不符"
    if cert["validity_mode"] == "FIXED":
        if not str(cert["valid_from"]) <= day <= str(cert["valid_until"]):
            return "证件未生效或已到期"
    elif cert["validity_mode"] not in {"LONG_TERM", "NOT_STATED"}:
        return "证件有效期待核实"
    return ""


def scope_problems(
    config: dict[str, Any],
    scope: dict[str, Any],
    certs: dict[str, Any],
    channel: str,
    day: str,
    *,
    preview: bool = False,
) -> list[str]:
    from medinv.modules.catalog.application.party_certificates import TYPES

    reasons = []
    exceptions = {e["rule_id"] for e in scope.get("exceptions", [])}
    selected = [certs[c] for c in scope["certificate_ids"] if c in certs]
    applicable = applicable_rules(config, scope, channel, day)
    if not applicable and not any(
        r["category"] == scope["category"]
        and scope["subject_type"] in r["subjects"]
        and channel in r["channels"]
        and (not r["device_classes"] or scope.get("device_class") in r["device_classes"])
        for r in config["rules"]
    ):
        reasons.append("此单位业务身份尚未配置证件规则，请在质量与准入中补充")
    for rule in applicable:
        if rule["id"] in exceptions:
            continue
        for group in rule["certificate_groups"]:
            candidates = [c for c in selected if c["certificate_type"] in group]
            if not any(not certificate_issue(c, channel, day, preview=preview) for c in candidates):
                names = " / ".join(TYPES[t] for t in group)
                detail = (
                    "；".join(
                        dict.fromkeys(
                            certificate_issue(c, channel, day, preview=preview) for c in candidates
                        )
                    )
                    if candidates
                    else "未关联所需证件"
                )
                reasons.append(f"{rule['name']}：{names}，{detail}")
    return reasons


def validate_profile(repo: SqlSession, party: dict[str, Any], payload: dict[str, Any]) -> None:
    ScopeProfile.model_validate(payload)
    certs = certificate_rows(
        repo,
        "party",
        party["id"],
        repo.rows("SELECT * FROM med.party_certificate WHERE party_id=:p", {"p": party["id"]}),
    )
    ids = {str(c["id"]) for c in certs}
    allowed = {
        "SUPPLIER": {"PURCHASE"},
        "CUSTOMER": {"WHOLESALE"},
        "BOTH": {"PURCHASE", "WHOLESALE"},
    }[party["kind"]]
    rules = {r["id"] for r in current_rules(repo)["payload"]["rules"]}
    for scope in payload["scopes"]:
        if not set(scope["channels"]) <= allowed or scope["category"] not in party["categories"]:
            raise BusinessError(
                "SCOPE_ROLE_MISMATCH", "范围的供销业务或类别与往来单位身份不符", 422
            )
        if not set(scope["certificate_ids"]) <= ids:
            raise BusinessError("NOT_FOUND", "关联证件不可见或不属于此单位", 404)
        if any(e["rule_id"] not in rules for e in scope.get("exceptions", [])):
            raise BusinessError("RULE_NOT_FOUND", "例外依据须对应现有准入规则", 422)


def evaluate(
    repo: SqlSession,
    entity: UUID,
    channel: str,
    party: dict[str, Any],
    products: list[dict[str, Any]],
    *,
    own_policy: dict[str, Any] | None = None,
    draft: bool = False,
) -> dict[str, Any] | None:
    from medinv.modules.catalog.application.admission import policy
    from medinv.modules.catalog.application.party_certificates import today

    if not party.get("id"):
        return None
    registered = profile(repo, party["id"])
    if not registered:
        return None
    rule_set = current_rules(repo)
    config, day = rule_set["payload"], str(today(repo))
    scopes = [
        s for s in registered["payload"]["scopes"] if s["enabled"] and channel in s["channels"]
    ]
    certs = {
        str(c["id"]): c
        for c in repo.rows(
            "SELECT * FROM med.party_certificate WHERE party_id=:p ORDER BY id FOR SHARE",
            {"p": party["id"]},
        )
    }
    scope_results = {s["id"]: scope_problems(config, s, certs, channel, day) for s in scopes}
    # Minimal reservation rows receive classifications from the locked live master.
    ids = [UUID(str(p["id"])) for p in products]
    classifiers = (
        {
            str(p["id"]): p
            for p in repo.rows(
                "SELECT s.id,p.device_class,p.device_classification_code,"
                "p.device_classification_version FROM med.sku s JOIN med.product p "
                "ON (s.tenant_id,s.product_id)=(p.tenant_id,p.id) "
                "WHERE s.id=ANY(:ids) ORDER BY s.id FOR SHARE OF s,p",
                {"ids": ids},
            )
        }
        if ids
        else {}
    )
    own = own_policy or policy(repo, entity, channel)
    own_payload = own["payload" if draft else "active_payload"]
    items: list[dict[str, Any]] = []
    issues: list[str] = []
    for product in products:
        values = {**product, **classifiers.get(str(product["id"]), {})}
        candidates = [s for s in scopes if scope_matches(s, values)]
        good = next((s for s in candidates if not scope_results[s["id"]]), None)
        problems = []
        if party["state"] != "APPROVED" or party["kind"] not in {
            "BOTH",
            "SUPPLIER" if channel == "PURCHASE" else "CUSTOMER",
        }:
            problems.append("往来单位主体未审核、已停用或供销身份不符")
        if not candidates:
            problems.append(
                "往来单位无覆盖此商品的已审核经营范围（请核对管理类别、目录版本和分类编码）"
            )
        elif not good:
            problems.extend(dict.fromkeys(p for s in candidates for p in scope_results[s["id"]]))
        if config["impact"] == "PARTY" and any(scope_results.values()):
            problems.append("已配置按单位控制：该单位存在受限经营范围")
        if config["require_own_scope"]:
            if not own_payload["enabled"]:
                problems.append("本企业对应业务的经营范围尚未启用")
            elif not any(
                scope_matches(g, values) and g["valid_from"] <= day <= g["valid_until"]
                for g in own_payload["own_scopes"]
            ):
                problems.append("本企业无覆盖此商品的有效经营范围")
        qualified = not problems
        passed = qualified or config["enforcement"] != "BLOCK"
        item = {
            "sku_id": str(product["id"]),
            "name": product["name"],
            "passed": passed,
            "qualified": qualified,
            "reasons": problems,
            "scope": good["name"] if good else candidates[0]["name"] if candidates else "未覆盖",
        }
        items.append(item)
        issues.extend(f"{product['name']}：{p}" for p in problems)
        product["admission"] = jsonable_encoder(
            {
                "mode": "SCOPED",
                "rules_version": str(rule_set["version"]),
                "channel": channel,
                "checked_on": day,
                "scope_profile_version": str(registered["version"]),
                "party_scope": good,
                "certificate_evidence": [certs[c] for c in good["certificate_ids"] if c in certs]
                if good
                else [],
                "own_policy_version": str(own["version" if draft else "active_version"]),
                "enforcement": config["enforcement"],
                "qualified": qualified,
                "issues": problems,
            }
        )
    return {
        "enabled": True,
        "mode": "SCOPED",
        "source": "DRAFT" if draft else "ACTIVE",
        "enforcement": config["enforcement"],
        "impact": config["impact"],
        "passed": all(i["passed"] for i in items),
        "qualified": not issues,
        "items": items,
        "issues": issues,
        "message": "按已审核范围逐商品检查；未拦截不代表资质合格",
    }


class PartyScopes:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def get(self, ctx: TenantContext, target: UUID) -> dict[str, Any]:
        from medinv.modules.catalog.application.maintenance import CatalogMaintenance
        from medinv.modules.catalog.application.party_certificates import today

        CatalogMaintenance(self.uow).scope(ctx, "party", target, "catalog.read")
        with self.uow.open(ctx, readonly=True) as session:
            repo = SqlSession(session)
            row = CatalogMaintenance.record(repo, "party", target)
            current = profile(repo, target)
            active = current["payload"] if current else {"scopes": []}
            draft = pending(repo, "party", target)
            payload = (draft or {}).get("payload", {}).get("scopes", active)
            config = current_rules(repo)["payload"]
            live_certs = repo.rows(
                "SELECT * FROM med.party_certificate WHERE party_id=:p", {"p": target}
            )
            certs = {str(c["id"]): c for c in certificate_rows(repo, "party", target, live_certs)}
            result = []
            for scope in payload["scopes"]:
                reasons = {
                    channel: scope_problems(
                        config, scope, certs, channel, str(today(repo)), preview=True
                    )
                    for channel in scope["channels"]
                }
                result.append(
                    {"id": scope["id"], "reasons": reasons, "qualified": not any(reasons.values())}
                )
            return dict(
                jsonable_encoder(
                    {
                        "version": str(row["version"]),
                        "payload": payload,
                        "active_payload": active,
                        "has_changes": bool(draft),
                        "legacy": current is None,
                        "rules": config,
                        "results": result,
                        "party_kind": row["kind"],
                    }
                )
            )

    def save(self, ctx: TenantContext, target: UUID, body: ScopeSave, key: str) -> dict[str, Any]:
        from medinv.modules.catalog.application.maintenance import CatalogMaintenance

        CatalogMaintenance(self.uow).scope(ctx, "party", target, "catalog.write")

        def run(repo: SqlSession) -> dict[str, Any]:
            row = CatalogMaintenance.record(repo, "party", target, lock=True)
            if str(row["version"]) != body.expected_version:
                raise BusinessError("VERSION_CONFLICT", "主体资料或范围已变化，请刷新", 409)
            payload = body.payload.model_dump(mode="json")
            validate_profile(repo, editable(repo, "party", row), payload)
            old = profile(repo, target)
            return stage(
                repo,
                ctx,
                "party",
                row,
                "scopes",
                payload,
                body.reason,
                old["payload"] if old else {"scopes": []},
            )

        return command(
            self.uow,
            ctx,
            "catalog.party.scopes",
            str(target),
            key,
            body.model_dump(mode="json"),
            run,
        )
