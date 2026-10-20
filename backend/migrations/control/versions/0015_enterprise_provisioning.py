from alembic import op

revision = "c0015"
down_revision = "c0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE control.enterprise_provisioning (
      request_id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL UNIQUE REFERENCES control.tenant(id),
      request_hash char(64) NOT NULL,
      plan jsonb NOT NULL,
      state varchar(12) NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','COMPLETED')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      completed_at timestamptz,
      CHECK((state='COMPLETED')=(completed_at IS NOT NULL))
    );
    CREATE FUNCTION control.protect_provisioning() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable provisioning request'; END IF;
      IF OLD.state='COMPLETED' OR NEW.request_id<>OLD.request_id
        OR NEW.tenant_id<>OLD.tenant_id OR NEW.request_hash<>OLD.request_hash
        OR NEW.plan<>OLD.plan OR NEW.created_at<>OLD.created_at THEN
        RAISE EXCEPTION 'immutable provisioning plan';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_provisioning BEFORE UPDATE OR DELETE
      ON control.enterprise_provisioning FOR EACH ROW
      EXECUTE FUNCTION control.protect_provisioning();
    """)
    # Provisioning is an operator-only entry point. No grants to web/auth/runtime roles.


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
