from alembic import op

revision = "b0043"
down_revision = "b0042"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.catalog_dictionary DROP CONSTRAINT catalog_dictionary_key_check;
      ALTER TABLE med.catalog_dictionary ADD CONSTRAINT catalog_dictionary_key_check
        CHECK(key IN ('dosage_form','trade_name','storage_conditions','base_unit',
          'recommendation_level','abc_class','drug_business_scope','device_business_scope',
          'purchase_approval','inspection_qualified','inspection_mixed','inspection_rejected'));
    """)


def downgrade():
    raise RuntimeError("Forward-only migration: preserve maintained purchase conclusions")
