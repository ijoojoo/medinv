"""Return application rights are specific to the persisted source kind."""

from medinv.platform.tenancy.context import TenantContext

APPLICATION_PERMISSIONS = {
    "PURCHASE": "returns.purchase.write",
    "SALES": "returns.sales.write",
    "RETAIL": "returns.retail.write",
}


def application_permission(ctx: TenantContext, kind: str | None = None) -> str:
    if "returns.write" in ctx.permissions:
        return "returns.write"
    if kind is None:
        # Reject unrelated callers before looking up an arbitrary document ID.
        for permission in APPLICATION_PERMISSIONS.values():
            if permission in ctx.permissions:
                return permission
        ctx.require("returns.write")
    permission = APPLICATION_PERMISSIONS.get(kind or "", "returns.write")
    ctx.require(permission)
    return permission


def custody_permission(ctx: TenantContext) -> str:
    permission = "returns.write" if "returns.write" in ctx.permissions else "returns.custody"
    ctx.require(permission)
    return permission
