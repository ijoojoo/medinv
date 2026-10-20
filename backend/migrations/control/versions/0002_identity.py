from alembic import op

revision = "c0002"
down_revision = "c0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE control.tenant (
      id uuid PRIMARY KEY, name varchar(120) NOT NULL,
      active boolean NOT NULL DEFAULT true
    );
    CREATE TABLE control.principal (
      id uuid PRIMARY KEY, issuer text NOT NULL, subject varchar(200) NOT NULL,
      display_name varchar(120) NOT NULL, UNIQUE (issuer,subject)
    );
    CREATE TABLE control.entity (
      tenant_id uuid NOT NULL REFERENCES control.tenant(id), id uuid NOT NULL,
      name varchar(120) NOT NULL, PRIMARY KEY (tenant_id,id)
    );
    INSERT INTO control.tenant(id,name)
      SELECT DISTINCT tenant_id,'Legacy tenant' FROM control.membership;
    INSERT INTO control.principal(id,issuer,subject,display_name)
      SELECT DISTINCT principal_id,'legacy:',principal_id::text,'Legacy principal'
      FROM control.membership;
    ALTER TABLE control.membership ADD COLUMN role_codes text[] NOT NULL DEFAULT '{}';
    ALTER TABLE control.membership ADD FOREIGN KEY (tenant_id) REFERENCES control.tenant(id);
    ALTER TABLE control.membership ADD FOREIGN KEY (principal_id) REFERENCES control.principal(id);
    ALTER TABLE control.web_session
      ADD COLUMN sealed_tokens text,
      ADD COLUMN token_expires_at timestamptz,
      ADD COLUMN refresh_id uuid,
      ADD COLUMN refresh_until timestamptz,
      ADD COLUMN token_version bigint NOT NULL DEFAULT 1;
    CREATE TABLE control.login_attempt (
      state_hash char(64) PRIMARY KEY, browser_hash char(64) NOT NULL,
      sealed_payload text NOT NULL, expires_at timestamptz NOT NULL,
      consumed_at timestamptz
    );
    CREATE INDEX login_expiry ON control.login_attempt(expires_at);
    CREATE TABLE control.audit_event (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES control.tenant(id),
      actor_id uuid NOT NULL REFERENCES control.principal(id),
      action varchar(80) NOT NULL, target_id uuid NOT NULL,
      detail jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE FUNCTION control.reject_fact_mutation() RETURNS trigger LANGUAGE plpgsql
      AS $$ BEGIN RAISE EXCEPTION 'immutable control audit'; END $$;
    CREATE TRIGGER immutable_control_audit BEFORE UPDATE OR DELETE ON control.audit_event
      FOR EACH ROW EXECUTE FUNCTION control.reject_fact_mutation();
    GRANT SELECT ON control.tenant TO control_runtime;
    GRANT USAGE ON SCHEMA control TO control_auth;
    GRANT SELECT ON ALL TABLES IN SCHEMA control TO control_auth;
    REVOKE ALL ON control.alembic_version FROM control_auth;
    GRANT INSERT, UPDATE ON control.web_session,control.login_attempt TO control_auth;
    GRANT UPDATE ON control.membership TO control_auth;
    GRANT INSERT ON control.audit_event TO control_auth;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
