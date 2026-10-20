from alembic import op

revision = "b0029"
down_revision = "b0028"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.document_draft (
        tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
        channel varchar(10) NOT NULL CHECK(channel IN ('purchase','sale')),
        created_by uuid NOT NULL,title varchar(120) NOT NULL DEFAULT '',
        source_order_id uuid,source_version varchar(19),
        content jsonb NOT NULL
          CHECK(jsonb_typeof(content)='object' AND octet_length(content::text)<=100000),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        state varchar(10) NOT NULL DEFAULT 'EDITING'
          CHECK(state IN ('EDITING','CONVERTED','DISCARDED')),
        converted_order_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
        CHECK((source_order_id IS NULL)=(source_version IS NULL)),
        CHECK((state='CONVERTED')=(converted_order_id IS NOT NULL))
      );
      CREATE INDEX ix_document_draft_owner ON med.document_draft
        (tenant_id,created_by,entity_id,channel,updated_at DESC,id) WHERE state='EDITING';
      ALTER TABLE med.document_draft ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.document_draft FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.document_draft
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.document_draft TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
