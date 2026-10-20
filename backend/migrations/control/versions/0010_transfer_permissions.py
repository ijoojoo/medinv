from alembic import op

revision = "c0010"
down_revision = "c0009"
branch_labels = None
depends_on = None


def upgrade():
    for role, permissions in {
        "OWNER": [
            "transfers.read",
            "transfers.write",
            "transfers.review",
            "transfers.dispatch",
            "transfers.receive",
            "warehouses.write",
        ],
        "ADMIN": [
            "transfers.read",
            "transfers.write",
            "transfers.review",
            "transfers.dispatch",
            "transfers.receive",
            "warehouses.write",
        ],
        "OPERATOR": [
            "transfers.read",
            "transfers.write",
            "transfers.dispatch",
            "transfers.receive",
        ],
        "QUALITY": ["transfers.read", "transfers.review"],
        "FINANCE": ["transfers.read"],
        "VIEWER": ["transfers.read"],
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
