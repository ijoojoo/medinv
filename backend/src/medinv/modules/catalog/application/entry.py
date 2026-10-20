from typing import Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder

from medinv.modules.catalog.application.document_master import attach
from medinv.modules.catalog.application.master_config import current_schema
from medinv.modules.catalog.application.profile import PROFILE_SELECT, project_business_identity
from medinv.modules.procurement.application.document_projection import capture, master_values
from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.tenancy.context import TenantContext


class EntryProducts:
    def __init__(self, uow: TenantUnitOfWork):
        self.uow = uow

    def search(
        self,
        ctx: TenantContext,
        entity: UUID,
        channel: Literal["purchase", "sale"],
        search: str,
        offset: int = 0,
        sku_id: UUID | None = None,
        match_barcode: bool = True,
    ) -> dict[str, Any]:
        ctx.require("purchase.read" if channel == "purchase" else "sales.read", entity)
        with self.uow.open(ctx, readonly=True) as session:
            rows = SqlSession(session).rows(
                """WITH barcode AS (
                  SELECT c.tenant_id,c.sku_id,c.packaging_id,c.external_code,
                    k.unit_name,k.numerator,k.denominator,
                    coalesce(c.packaging_id IS NULL OR (k.state='APPROVED'
                      AND k.effective_until IS NULL),false) AS available
                  FROM med.catalog_code c LEFT JOIN med.packaging k
                    ON (k.tenant_id,k.id,k.sku_id)=(c.tenant_id,c.packaging_id,c.sku_id)
                  WHERE :match_barcode AND CAST(:sku AS uuid) IS NULL
                    AND c.scheme='BARCODE' AND c.namespace='' AND c.state='ACTIVE'
                    AND c.external_code=:barcode AND c.sku_id IS NOT NULL
                ), candidates AS (
                  SELECT s.id,s.code,s.base_unit,s.qty_scale,p.name,p.code AS product_code,
                    p.specification,p.manufacturer_name,p.generic_name,p.search_alias,
                    p.trade_name,p.device_model,
                    CASE WHEN b.sku_id IS NOT NULL THEN jsonb_build_object(
                      'barcode',b.external_code,'packaging_id',b.packaging_id,
                      'unit_name',coalesce(b.unit_name,s.base_unit),
                      'numerator',coalesce(b.numerator,1)::text,
                      'denominator',coalesce(b.denominator,1)::text,
                      'available',coalesce(b.available,false)) END AS barcode_match,
                    coalesce(p.approval_state='APPROVED' AND p.restricted_category IS NULL
                      AND coalesce(b.available,true)
                      AND NOT s.serial_required AND (:channel='purchase' OR s.lot_required)
                      AND med.product_qualification_current(p)
                      AND NOT EXISTS(SELECT 1 FROM med.product_entity_settings x
                        WHERE (x.tenant_id,x.product_id)=(p.tenant_id,p.id) AND x.entity_id=:e
                        AND (NOT x.enabled OR (:channel='purchase' AND NOT x.purchase_enabled)
                        OR (:channel='sale' AND NOT x.sales_enabled)))
                      AND NOT EXISTS (SELECT FROM med.stock_gate g WHERE g.sku_id=s.id
                        AND g.tenant_id=s.tenant_id AND g.entity_id=:e AND g.blocked)
                      AND EXISTS (SELECT FROM med.legal_entity e
                        WHERE e.id=:e AND e.status='ACTIVE'),false) AS eligible
                  FROM med.sku s JOIN med.product p
                    ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                  LEFT JOIN barcode b ON (b.tenant_id,b.sku_id)=(s.tenant_id,s.id)
                ) SELECT * FROM candidates c
                  WHERE barcode_match IS NOT NULL OR (NOT EXISTS(SELECT FROM barcode)
                    AND (CAST(:sku AS uuid) IS NULL AND eligible OR id=CAST(:sku AS uuid))
                    AND NOT EXISTS (SELECT FROM unnest(CAST(:terms AS text[])) AS term
                      WHERE strpos(lower(concat_ws(' ',name,code,product_code,specification,
                        manufacturer_name,generic_name,search_alias,trade_name,device_model)),term)=0))
                  ORDER BY CASE WHEN lower(code)=:exact THEN 0 ELSE 1 END,name,id
                  LIMIT 21 OFFSET :offset""",
                {
                    "e": entity,
                    "channel": channel,
                    "sku": sku_id,
                    "terms": search.strip().lower().split(),
                    "exact": search.strip().lower(),
                    "offset": offset,
                    "barcode": search.strip(),
                    "match_barcode": match_barcode,
                },
            )
            if rows:
                repo = SqlSession(session)
                details = repo.rows(
                    f"""SELECT s.id,s.product_id,s.code,p.code AS product_code,
                    s.base_unit,p.name,p.category,
                    p.specification,p.manufacturer_name,p.approval_number,{PROFILE_SELECT}
                    FROM med.sku s JOIN med.product p
                    ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
                    WHERE s.id=ANY(:ids)""",
                    {"ids": [r["id"] for r in rows[:20]]},
                )
                project_business_identity(repo, details, enforce_rules=False)
                attach(repo, ctx, entity, details)
                schema = current_schema(repo)["payload"]
                projected = {r["id"]: r for r in details}
                for row in rows[:20]:
                    data = projected[row["id"]]
                    row["document_values"], row["document_status"] = master_values(
                        capture(data), schema, ctx.permissions
                    )
                    row["manufacturer_name"] = data["manufacturer_name"]
            return dict(jsonable_encoder({"items": rows[:20], "has_more": len(rows) > 20}))
