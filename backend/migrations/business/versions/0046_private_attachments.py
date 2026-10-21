from alembic import op

revision = "b0046"
down_revision = "b0045"
branch_labels = None
depends_on = None


def upgrade():
    # Binary content and metadata commit together and travel in the business DB backup.
    # Runtime has no UPDATE/DELETE grant: a replacement is a new immutable version.
    op.execute("""
      CREATE TABLE med.catalog_attachment (
        tenant_id uuid NOT NULL,id uuid NOT NULL,
        product_id uuid,party_id uuid,root_id uuid NOT NULL,previous_id uuid,
        version integer NOT NULL CHECK(version>0),
        filename varchar(180) NOT NULL CHECK(length(filename)>0),
        media_type varchar(40) NOT NULL CHECK(media_type IN
          ('application/pdf','image/png','image/jpeg')),
        byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 5242880),
        sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
        content bytea NOT NULL CHECK(octet_length(content)=byte_size),
        reason varchar(500) NOT NULL CHECK(length(btrim(reason))>0),
        created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),
        UNIQUE(tenant_id,root_id,version),UNIQUE(tenant_id,previous_id),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id),
        FOREIGN KEY(tenant_id,party_id) REFERENCES med.party(tenant_id,id),
        FOREIGN KEY(tenant_id,root_id) REFERENCES med.catalog_attachment(tenant_id,id),
        FOREIGN KEY(tenant_id,previous_id) REFERENCES med.catalog_attachment(tenant_id,id),
        CHECK(num_nonnulls(product_id,party_id)=1),
        CHECK((previous_id IS NULL AND root_id=id AND version=1) OR
          (previous_id IS NOT NULL AND root_id<>id AND version>1))
      );
      CREATE INDEX catalog_attachment_product ON med.catalog_attachment(tenant_id,product_id);
      CREATE INDEX catalog_attachment_party ON med.catalog_attachment(tenant_id,party_id);
      ALTER TABLE med.catalog_attachment ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.catalog_attachment FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.catalog_attachment
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT ON med.catalog_attachment TO med_runtime;
      ALTER TABLE med.product_certificate ADD COLUMN attachment_id uuid,
        ADD FOREIGN KEY(tenant_id,attachment_id) REFERENCES med.catalog_attachment(tenant_id,id);
      ALTER TABLE med.party_certificate ADD COLUMN attachment_id uuid,
        ADD FOREIGN KEY(tenant_id,attachment_id) REFERENCES med.catalog_attachment(tenant_id,id);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
