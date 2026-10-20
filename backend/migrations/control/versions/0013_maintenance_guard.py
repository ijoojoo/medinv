from alembic import op

revision = "c0013"
down_revision = "c0012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION control.lock_maintenance_tenant(p_id uuid) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE enabled boolean;
      BEGIN
        SELECT active INTO enabled FROM control.tenant WHERE id=p_id FOR SHARE;
        RETURN coalesce(enabled,false);
      END $$;
      REVOKE ALL ON FUNCTION control.lock_maintenance_tenant(uuid) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION control.lock_maintenance_tenant(uuid) TO control_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
