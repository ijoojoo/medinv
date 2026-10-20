from alembic import op

revision = "b0036"
down_revision = "b0035"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      DO $$ DECLARE constraint_row record; BEGIN
      FOR constraint_row IN SELECT conname FROM pg_constraint
        WHERE conrelid='med.product_certificate'::regclass AND contype='c'
        AND pg_get_constraintdef(oid) LIKE '%certificate_type%'
      LOOP EXECUTE format('ALTER TABLE med.product_certificate DROP CONSTRAINT %I',constraint_row.conname);
      END LOOP; END $$;
      ALTER TABLE med.product_certificate ADD CHECK(certificate_type IN
        ('DRUG_APPROVAL','DEVICE_REGISTRATION','DEVICE_FILING','HEALTH_REGISTRATION','HEALTH_FILING',
         'FSMP_REGISTRATION','INFANT_FORMULA_REGISTRATION','COSMETIC_FILING','COSMETIC_REGISTRATION'));
      ALTER TABLE med.product_certificate ADD CHECK(state<>'VERIFIED' OR
        (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND validity_mode<>'UNCONFIRMED'
         AND length(btrim(holder_name))>0 AND length(btrim(issuer))>0 AND length(btrim(evidence_ref))>0
         AND (certificate_type IN ('DEVICE_FILING','HEALTH_FILING','COSMETIC_FILING') OR validity_mode='FIXED')));
      CREATE FUNCTION med.reject_service_sku() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF EXISTS(SELECT 1 FROM med.product WHERE tenant_id=NEW.tenant_id
        AND id=NEW.product_id AND category='SERVICE') THEN
        RAISE EXCEPTION 'Outpatient services cannot have inventory SKUs'; END IF; RETURN NEW; END $$;
      CREATE TRIGGER sku_physical_only BEFORE INSERT OR UPDATE ON med.sku
        FOR EACH ROW EXECUTE FUNCTION med.reject_service_sku();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
