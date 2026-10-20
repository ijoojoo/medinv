"""Age one newly created software-test reservation, then run the real worker scoped to it.

Browser tests use actual APIs for all business facts. This helper only controls time for
the exact fixture; it is not a public endpoint and never processes unrelated records.
"""
import os
import sys
from uuid import UUID

from dev import environment

os.environ.update(environment())

from sqlalchemy import text
from medinv.entrypoints.expiry_worker import ExpiryWorker
from medinv.modules.inventory.application.expiry import ReservationExpiry
from medinv.modules.inventory.application.service import InventoryService
from medinv.platform.auth.maintenance import MaintenanceGuard, expiry_context
from medinv.platform.config import Settings
from medinv.platform.database.uow import TenantUnitOfWork, make_engine, check_runtime_role

tenant, entity, rid, sku = (UUID(value) for value in sys.argv[1:])
config = Settings()
assert os.environ['MED_ENVIRONMENT'] == 'development'
business = make_engine(config.database_url.get_secret_value(), config)
control = make_engine(config.control_database_url.get_secret_value(), config)
try:
    check_runtime_role(business, 'med')
    check_runtime_role(control, 'control')
    uow = TenantUnitOfWork(business, config)
    ctx = expiry_context(tenant, entity)
    with uow.open(ctx) as session:
        row = session.execute(text("""
          SELECT r.id FROM med.reservation r
          WHERE r.id=:id AND r.entity_id=:e AND r.state='ACTIVE'
            AND r.created_at>now()-interval '10 minutes'
            AND EXISTS (SELECT FROM med.reservation_allocation a
              JOIN med.stock_balance b ON (b.tenant_id,b.id)=(a.tenant_id,a.balance_id)
              JOIN med.sku s ON (s.tenant_id,s.id)=(b.tenant_id,b.sku_id)
              JOIN med.product p ON (p.tenant_id,p.id)=(s.tenant_id,s.product_id)
              WHERE a.reservation_id=r.id AND a.tenant_id=r.tenant_id AND s.id=:sku
                AND s.code LIKE 'SS-%' AND p.name LIKE '销售验收器械-%'
                AND p.manufacturer_name='软件测试厂家')
        """), dict(id=rid, e=entity, sku=sku)).scalar_one()
        assert row == rid
        deadline = session.execute(text("""UPDATE med.reservation
          SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id
          RETURNING expires_at"""), dict(id=rid)).scalar_one()

    class FixtureGuard(MaintenanceGuard):
        def tenants(self, after, limit=10):
            return [tenant]

    class FixtureExpiry(ReservationExpiry):
        def candidates(self, ctx, after, limit):
            rows = super().candidates(ctx, (deadline, UUID(int=0)), limit)
            return [r for r in rows if r['id'] == rid]

    worker = ExpiryWorker(FixtureExpiry(InventoryService(uow)), FixtureGuard(control), config)
    result = worker.tick()
    assert result == dict(scanned=1, released=1, skipped=0, errors=0), result
    print('Exact test fixture expired through the guarded worker transaction.')
finally:
    business.dispose()
    control.dispose()
