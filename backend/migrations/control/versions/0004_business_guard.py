from alembic import op

revision = "c0004"
down_revision = "c0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
               CREATE FUNCTION control.lock_business_session(p_hash text) RETURNS boolean
               LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
               DECLARE t uuid; p uuid;
               BEGIN
               SELECT tenant_id,principal_id INTO t,p FROM control.web_session WHERE
               token_hash=p_hash;
               IF t IS NULL THEN RETURN false; END IF;
               PERFORM id FROM control.tenant WHERE id=t FOR SHARE;
               PERFORM principal_id FROM control.membership WHERE tenant_id=t AND
               principal_id=p FOR SHARE;
               PERFORM token_hash FROM control.web_session WHERE token_hash=p_hash FOR
               SHARE;
               RETURN EXISTS(SELECT FROM control.web_session
               WHERE token_hash=p_hash AND tenant_id=t AND principal_id=p);
               END $$;
               REVOKE ALL ON FUNCTION control.lock_business_session(text) FROM PUBLIC;
               GRANT EXECUTE ON FUNCTION control.lock_business_session(text) TO
               control_runtime;
               """)
    additions = {
        "OWNER": [
            "catalog.read",
            "catalog.write",
            "catalog.review",
            "purchase.read",
            "purchase.write",
            "purchase.approve",
            "purchase.receive",
            "purchase.inspect",
            "purchase.post",
        ],
        "ADMIN": [
            "catalog.read",
            "catalog.write",
            "catalog.review",
            "purchase.read",
            "purchase.write",
            "purchase.approve",
            "purchase.receive",
            "purchase.inspect",
            "purchase.post",
        ],
        "OPERATOR": [
            "catalog.read",
            "catalog.write",
            "purchase.read",
            "purchase.write",
            "purchase.receive",
        ],
        "VIEWER": ["catalog.read", "purchase.read"],
    }
    # Existing permission grants remain; the role migration deliberately revokes old sessions.
    for role, permissions in additions.items():
        grants = ",".join("'" + p + "'" for p in permissions)
        op.execute(f"""UPDATE control.membership SET permissions=ARRAY(
            SELECT DISTINCT unnest(permissions || ARRAY[{grants}]) ORDER BY 1),
            permission_version=permission_version+1 WHERE '{role}'=ANY(role_codes)""")
    op.execute("""UPDATE control.web_session s SET revoked_at=clock_timestamp(),
               sealed_tokens=NULL,
               refresh_id=NULL,refresh_until=NULL FROM control.membership m
               WHERE (s.tenant_id,s.principal_id)=(m.tenant_id,m.principal_id)
               AND s.permission_version<>m.permission_version AND s.revoked_at IS NULL""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
