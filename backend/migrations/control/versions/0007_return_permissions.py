from alembic import op

revision = "c0007"
down_revision = "c0006"
branch_labels = None
depends_on = None


def upgrade():
    for role, permissions in {
        "OWNER": ["returns.read", "returns.write", "returns.review", "returns.post"],
        "ADMIN": ["returns.read", "returns.write", "returns.review", "returns.post"],
        "OPERATOR": ["returns.read", "returns.write"],
        "QUALITY": ["returns.read", "returns.review", "returns.post"],
        "FINANCE": ["returns.read"],
        "VIEWER": ["returns.read"],
    }.items():
        grants = ",".join("'" + p + "'" for p in permissions)
        op.execute(f"""UPDATE control.membership SET permissions=ARRAY(
          SELECT DISTINCT unnest(permissions || ARRAY[{grants}]) ORDER BY 1),
          permission_version=permission_version+1 WHERE '{role}'=ANY(role_codes)""")
    op.execute("""UPDATE control.web_session s SET revoked_at=clock_timestamp(),sealed_tokens=NULL,
      refresh_id=NULL,refresh_until=NULL FROM control.membership m
      WHERE (s.tenant_id,s.principal_id)=(m.tenant_id,m.principal_id)
      AND s.permission_version<>m.permission_version AND s.revoked_at IS NULL""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
