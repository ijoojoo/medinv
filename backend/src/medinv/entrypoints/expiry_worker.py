"""Bounded, restartable expiry worker using the same non-owner database roles as the API."""

import argparse
import json
import logging
import signal
import threading
import time
from collections import OrderedDict
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from medinv.modules.inventory.application.expiry import START, ReservationExpiry
from medinv.modules.inventory.application.service import InventoryService
from medinv.platform.auth.maintenance import MaintenanceGuard, expiry_context
from medinv.platform.config import Settings
from medinv.platform.database.uow import TenantUnitOfWork, check_runtime_role, make_engine
from medinv.platform.errors import BusinessError

logger = logging.getLogger("medinv.expiry")


class ExpiryWorker:
    def __init__(self, service: ReservationExpiry, guard: MaintenanceGuard, settings: Settings):
        self.service, self.guard, self.settings = service, guard, settings
        self.tenant_cursor = UUID(int=0)
        self.cursors: OrderedDict[UUID, tuple[datetime, UUID]] = OrderedDict()

    def tick(self) -> dict[str, int]:
        totals = dict(scanned=0, released=0, skipped=0, errors=0)
        if not self.settings.reservation_expiry_enabled:
            return totals
        deadline = time.monotonic() + 5
        tenants = self.guard.tenants(self.tenant_cursor)
        if not tenants:
            self.tenant_cursor = UUID(int=0)
        for tenant in tenants:
            if time.monotonic() >= deadline:
                break
            self.tenant_cursor = tenant
            try:
                rows = self.service.candidates(
                    expiry_context(tenant),
                    self.cursors.get(tenant, START),
                    self.settings.reservation_expiry_batch_size,
                )
            except (BusinessError, SQLAlchemyError) as exc:
                totals["errors"] += 1
                logger.warning("expiry scan failed tenant=%s type=%s", tenant, type(exc).__name__)
                continue
            for row in rows:
                if time.monotonic() >= deadline:
                    break
                totals["scanned"] += 1
                # A busy or bad old row cannot starve later candidates forever.
                self.cursors[tenant] = (row["expires_at"], row["id"])
                self.cursors.move_to_end(tenant)
                try:
                    with self.guard.hold(tenant):
                        released = self.service.expire(
                            expiry_context(tenant, row["entity_id"]), row["id"]
                        )
                    totals["released" if released else "skipped"] += 1
                except (BusinessError, SQLAlchemyError) as exc:
                    totals["errors"] += 1
                    code = exc.code if isinstance(exc, BusinessError) else type(exc).__name__
                    logger.warning(
                        "expiry item failed tenant=%s reservation=%s code=%s",
                        tenant,
                        row["id"],
                        code,
                    )
            else:
                if len(rows) < self.settings.reservation_expiry_batch_size:
                    self.cursors.pop(tenant, None)
            while len(self.cursors) > 1000:
                self.cursors.popitem(last=False)
        return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one bounded scan then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()  # type: ignore[call-arg]
    if not settings.reservation_expiry_enabled:
        raise SystemExit("Reservation expiry is disabled; set MED_RESERVATION_EXPIRY_ENABLED=true")
    business = make_engine(settings.database_url.get_secret_value(), settings)
    control = make_engine(settings.control_database_url.get_secret_value(), settings)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        check_runtime_role(business, "med")
        check_runtime_role(control, "control")
        worker = ExpiryWorker(
            ReservationExpiry(InventoryService(TenantUnitOfWork(business, settings))),
            MaintenanceGuard(control),
            settings,
        )
        logger.info(
            "Reservation expiry worker started; interval=%ss batch=%s",
            settings.reservation_expiry_interval_seconds,
            settings.reservation_expiry_batch_size,
        )
        while not stop.is_set():
            try:
                totals = worker.tick()
                if args.once or totals["scanned"] or totals["errors"]:
                    logger.info("expiry pass %s", json.dumps(totals))
                if args.once and totals["errors"]:
                    raise SystemExit(1)
            except (BusinessError, SQLAlchemyError) as exc:
                logger.warning("expiry pass unavailable type=%s", type(exc).__name__)
                if args.once:
                    raise SystemExit(1) from None
            if args.once or stop.wait(settings.reservation_expiry_interval_seconds):
                break
    finally:
        business.dispose()
        control.dispose()


if __name__ == "__main__":
    main()
