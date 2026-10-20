from alembic import op

revision = "b0044"
down_revision = "b0043"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.sales_order_line ADD COLUMN document_snapshot jsonb;
      ALTER TABLE med.document_column_profile
        DROP CONSTRAINT document_column_profile_document_type_check;
      ALTER TABLE med.document_column_profile
        ADD CONSTRAINT document_column_profile_document_type_check
        CHECK(document_type IN ('orders','receiving','inspection','posting','sales_orders'));
      ALTER TABLE med.document_column_profile
        DROP CONSTRAINT document_column_profile_template_check;
      ALTER TABLE med.document_column_profile ADD CONSTRAINT document_column_profile_template_check
        CHECK(jsonb_typeof(template)='object' AND octet_length(template::text)<=160000);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
