from alembic import op

revision = "b0027"
down_revision = "b0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.product
        ADD COLUMN search_alias varchar(200) NOT NULL DEFAULT '',
        ADD COLUMN trade_name varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN storage_conditions varchar(500) NOT NULL DEFAULT '',
        ADD COLUMN manufacturer_address varchar(300) NOT NULL DEFAULT '',
        ADD COLUMN generic_name varchar(200) NOT NULL DEFAULT '',
        ADD COLUMN dosage_form varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN authorization_holder varchar(200) NOT NULL DEFAULT '',
        ADD COLUMN authorization_holder_address varchar(300) NOT NULL DEFAULT '',
        ADD COLUMN device_class varchar(10) NOT NULL DEFAULT 'UNKNOWN',
        ADD COLUMN device_model varchar(200) NOT NULL DEFAULT '',
        ADD COLUMN registrant_name varchar(200) NOT NULL DEFAULT '',
        ADD COLUMN registrant_address varchar(300) NOT NULL DEFAULT '',
        ADD CONSTRAINT product_device_class CHECK (device_class IN ('UNKNOWN','I','II','III')),
        ADD CONSTRAINT product_profile_category CHECK (
          (category='DRUG' AND device_class='UNKNOWN' AND device_model=''
            AND registrant_name='' AND registrant_address='') OR
          (category='DEVICE' AND generic_name='' AND dosage_form=''
            AND authorization_holder='' AND authorization_holder_address=''));
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
