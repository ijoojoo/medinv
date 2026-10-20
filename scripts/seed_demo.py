"""Idempotent local demo seed. Never imports live business records or resets existing data."""

import os
from uuid import NAMESPACE_URL, uuid4, uuid5

from dev import environment
from dev_accounts import ACCOUNTS
from medinv.platform.auth.roles import ROLE_PERMISSIONS
from medinv.platform.config import Settings
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.tenancy.context import TenantContext
from sqlalchemy import create_engine, text


def ident(name):
    return uuid5(NAMESPACE_URL, "medinv-dev/" + name)


def main():
    os.environ.update(environment())
    settings = Settings()
    control = create_engine(
        os.environ["MED_CONTROL_MIGRATION_URL"], hide_parameters=True
    )
    business = create_engine(
        settings.database_url.get_secret_value(), hide_parameters=True
    )
    tenants = [
        (
            "wholesale",
            "安心药械批发 · 演示",
            "批发经营主体",
            "中心仓库",
            "一次性医用口罩",
            "1200",
        ),
        (
            "retail",
            "安心零售药房 · 演示",
            "零售经营主体",
            "门店库房",
            "电子体温计",
            "36",
        ),
    ]
    owner = ident("demo.owner")
    viewer = ident("demo.viewer")
    quality = ident("demo.quality")
    try:
        with control.begin() as conn:
            conn.execute(text("SET LOCAL ROLE control_owner"))
            for username, name in ACCOUNTS:
                pid = ident(username)
                conn.execute(
                    text("""INSERT INTO control.principal(id,issuer,subject,display_name)
                  VALUES (:id,:issuer,:subject,:name) ON CONFLICT DO NOTHING"""),
                    {
                        "id": pid,
                        "issuer": os.environ["MED_OIDC_ISSUER_URL"],
                        "subject": str(pid),
                        "name": name,
                    },
                )
            for key, name, entity_name, *_ in tenants:
                tid, eid = ident(key), ident(key + "/entity")
                conn.execute(
                    text(
                        "INSERT INTO control.tenant(id,name) VALUES (:id,:name) ON CONFLICT DO NOTHING"
                    ),
                    {"id": tid, "name": name},
                )
                conn.execute(
                    text(
                        "INSERT INTO control.entity(tenant_id,id,name) VALUES (:t,:id,:name) ON CONFLICT DO NOTHING"
                    ),
                    {"t": tid, "id": eid, "name": entity_name},
                )
                members = (
                    [(owner, "OWNER"), (viewer, "VIEWER")]
                    if key == "wholesale"
                    else [(owner, "OWNER")]
                )
                members.append((quality, "QUALITY"))
                for pid, role in members:
                    conn.execute(
                        text("""INSERT INTO control.membership
                      (tenant_id,principal_id,entity_ids,permissions,role_codes)
                      VALUES (:t,:p,:e,:perms,:roles) ON CONFLICT DO NOTHING"""),
                        {
                            "t": tid,
                            "p": pid,
                            "e": [eid],
                            "perms": sorted(ROLE_PERMISSIONS[role]),
                            "roles": [role],
                        },
                    )
        uow = TenantUnitOfWork(business, settings)
        for key, _, entity_name, wh_name, product_name, qty in tenants:
            tid, eid = ident(key), ident(key + "/entity")
            ctx = TenantContext(
                tid,
                owner,
                uuid4(),
                1,
                1,
                frozenset([eid]),
                frozenset(["inventory.read"]),
            )
            values = {
                "tenant": tid,
                "actor": owner,
                "entity": eid,
                "entity_name": entity_name,
                "warehouse": ident(key + "/warehouse"),
                "wh_name": wh_name,
                "location": ident(key + "/location"),
                "product": ident(key + "/product"),
                "product_name": product_name,
                "sku": ident(key + "/sku"),
                "lot": ident(key + "/lot"),
                "balance": ident(key + "/balance"),
                "gate": ident(key + "/gate"),
                "qty": qty,
            }
            statements = [
                """INSERT INTO med.legal_entity(tenant_id,id,created_by,updated_by,name,credit_code,status)
                   VALUES (:tenant,:entity,:actor,:actor,:entity_name,'DEMO_ONLY','ACTIVE')""",
                """INSERT INTO med.warehouse(tenant_id,id,created_by,updated_by,entity_id,code,kind,name)
                   VALUES (:tenant,:warehouse,:actor,:actor,:entity,'DEMO-WH','PHYSICAL',:wh_name)""",
                """INSERT INTO med.location(tenant_id,id,created_by,updated_by,warehouse_id,code)
                   VALUES (:tenant,:location,:actor,:actor,:warehouse,'A-01')""",
                """INSERT INTO med.product(tenant_id,id,created_by,updated_by,code,name,category,
                     approval_state,manufacturer_name)
                   VALUES (:tenant,:product,:actor,:actor,'DEMO-P',:product_name,'DEVICE','APPROVED','仅供软件演示')""",
                """INSERT INTO med.sku(tenant_id,id,created_by,updated_by,product_id,code,base_unit,qty_scale)
                   VALUES (:tenant,:sku,:actor,:actor,:product,'DEMO-SKU','个',0)""",
                """INSERT INTO med.stock_gate(tenant_id,id,created_by,updated_by,entity_id,sku_id)
                   VALUES (:tenant,:gate,:actor,:actor,:entity,:sku)""",
                """INSERT INTO med.lot(tenant_id,id,created_by,updated_by,sku_id,manufacturer_key,
                     lot_no,expiry_kind,expiry_exclusive)
                   VALUES (:tenant,:lot,:actor,:actor,:sku,'DEMO','DEMO-2026-001','DATE','2028-01-01T00:00:00+08:00')""",
                """INSERT INTO med.stock_balance(tenant_id,id,created_by,updated_by,entity_id,owner_id,
                     warehouse_id,location_id,sku_id,lot_id,quality_status,on_hand)
                   VALUES (:tenant,:balance,:actor,:actor,:entity,:entity,:warehouse,:location,
                           :sku,:lot,'QUALIFIED',CAST(:qty AS numeric))""",
            ]
            with uow.open(ctx) as session:
                for sql in statements:
                    session.execute(text(sql + " ON CONFLICT DO NOTHING"), values)
    finally:
        control.dispose()
        business.dispose()
    print(
        "Two local demo enterprises seeded. Existing memberships and inventory were preserved."
    )


if __name__ == "__main__":
    main()
