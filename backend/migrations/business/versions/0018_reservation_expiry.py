from alembic import op

revision = "b0018"
down_revision = "b0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.reservation ADD COLUMN auto_released_at timestamptz,
        ADD CHECK (auto_released_at IS NULL OR (state='RELEASED' AND protected_until IS NULL));
      CREATE INDEX reservation_expiry_scan ON med.reservation(tenant_id,expires_at,id)
        WHERE state='ACTIVE' AND protected_until IS NULL;
      CREATE FUNCTION med.protect_auto_release() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.auto_released_at IS NOT NULL AND
           ROW(NEW.auto_released_at,NEW.state) IS DISTINCT FROM
           ROW(OLD.auto_released_at,OLD.state) THEN
          RAISE EXCEPTION 'automatic release fact is immutable';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_auto_release BEFORE UPDATE ON med.reservation
        FOR EACH ROW EXECUTE FUNCTION med.protect_auto_release();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
