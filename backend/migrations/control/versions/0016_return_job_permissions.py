from alembic import op

revision = "c0016"
down_revision = "c0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    WITH grants(role_code, permission) AS (VALUES
      ('PURCHASER','returns.purchase.write'), ('SALES','returns.sales.write'),
      ('CASHIER','returns.retail.write'), ('WAREHOUSE','returns.custody')
    ), desired AS (
      SELECT m.tenant_id,m.principal_id,ARRAY(
        SELECT DISTINCT p FROM unnest(m.permissions || ARRAY['returns.read'] || ARRAY(
          SELECT g.permission FROM grants g WHERE g.role_code=ANY(m.role_codes)
        )) p ORDER BY p
      ) AS permissions FROM control.membership m
      WHERE m.role_codes && ARRAY['PURCHASER','SALES','CASHIER','WAREHOUSE']::text[]
    )
    UPDATE control.membership m SET permissions=d.permissions,
      permission_version=m.permission_version+1 FROM desired d
    WHERE (m.tenant_id,m.principal_id)=(d.tenant_id,d.principal_id)
      AND m.permissions IS DISTINCT FROM d.permissions;
    """)
    op.execute("""UPDATE control.web_session s SET revoked_at=clock_timestamp(),sealed_tokens=NULL,
      refresh_id=NULL,refresh_until=NULL FROM control.membership m
      WHERE (s.tenant_id,s.principal_id)=(m.tenant_id,m.principal_id)
      AND s.permission_version<>m.permission_version AND s.revoked_at IS NULL""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
