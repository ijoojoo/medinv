from alembic import op

revision = "c0017"
down_revision = "c0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      UPDATE control.membership SET
        permissions=array_append(permissions,'catalog.configure'),
        permission_version=permission_version+1
      WHERE role_codes && ARRAY['OWNER','ADMIN']::text[]
        AND NOT ('catalog.configure'=ANY(permissions));
      UPDATE control.web_session s SET revoked_at=clock_timestamp(),sealed_tokens=NULL,
        refresh_id=NULL,refresh_until=NULL FROM control.membership m
      WHERE (s.tenant_id,s.principal_id)=(m.tenant_id,m.principal_id)
        AND s.permission_version<>m.permission_version AND s.revoked_at IS NULL;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
