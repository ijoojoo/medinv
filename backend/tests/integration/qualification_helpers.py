"""Tests use the parent review command; child endpoints never approve evidence."""

from medinv.modules.catalog.api.schemas import Review
from medinv.modules.catalog.application.maintenance import CatalogMaintenance
from medinv.modules.catalog.application.service import CatalogService


def review_parent(service, ctx, parent, certificate, body, key):
    kind = "party" if type(service).__name__ == "PartyCertificates" else "product"
    if body.decision == "INACTIVATE":
        return service.review(ctx, parent, certificate, body, key)
    row = CatalogMaintenance(service.uow).detail(ctx, kind, parent, 0)["record"]
    CatalogService(service.uow).review(
        ctx,
        kind,
        parent,
        Review(
            expected_version=row["version"],
            decision="APPROVE",
            evidence_ref=body.evidence_ref,
            reason=body.reason,
        ),
        key,
    )
    return service.detail(ctx, parent, certificate)["record"]
