from alembic import op

revision = "c0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE control.membership (
        tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
        active boolean NOT NULL DEFAULT true,
        permission_version bigint NOT NULL DEFAULT 1 CHECK (permission_version>0),
        entity_ids uuid[] NOT NULL, permissions text[] NOT NULL,
        PRIMARY KEY (tenant_id,principal_id)
      );
      CREATE TABLE control.web_session (
        token_hash char(64) PRIMARY KEY,
        tenant_id uuid NOT NULL, principal_id uuid NOT NULL,
        csrf_hash char(64) NOT NULL,
        context_generation bigint NOT NULL CHECK (context_generation>0),
        permission_version bigint NOT NULL CHECK (permission_version>0),
        expires_at timestamptz NOT NULL, revoked_at timestamptz,
        FOREIGN KEY (tenant_id,principal_id) REFERENCES control.membership(tenant_id,
                principal_id)
      );
      CREATE INDEX session_expiry ON control.web_session(expires_at);
      GRANT SELECT ON control.membership, control.web_session TO control_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
