from alembic import op

revision = "b0037"
down_revision = "b0036"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.product_certificate_link ADD COLUMN business_use boolean NOT NULL DEFAULT false;
      CREATE UNIQUE INDEX product_business_certificate ON med.product_certificate_link(tenant_id,product_id)
        WHERE business_use;
      CREATE FUNCTION med.product_qualification_current(p med.product) RETURNS boolean
      LANGUAGE sql STABLE SECURITY INVOKER AS $$
        SELECT p.record_status='ACTIVE' AND p.category<>'SERVICE' AND CASE
          WHEN p.form_revision IS NULL THEN COALESCE(
            (statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
              BETWEEN p.valid_from AND p.valid_until,false)
          WHEN p.category IN ('FOOD','DAILY') THEN true
          WHEN p.category='HERB' AND EXISTS(SELECT 1 FROM med.product_master_detail d
            WHERE (d.tenant_id,d.product_id)=(p.tenant_id,p.id)
              AND d.regulatory_subtype='ORDINARY_HERB') THEN true
          WHEN p.category='INFANT' AND EXISTS(SELECT 1 FROM med.product_master_detail d
            WHERE (d.tenant_id,d.product_id)=(p.tenant_id,p.id)
              AND d.regulatory_subtype='INFANT_OTHER') THEN true
          ELSE EXISTS(SELECT 1 FROM med.product_certificate_link l
            JOIN med.product_certificate c ON (c.tenant_id,c.id)=(l.tenant_id,l.certificate_id)
            WHERE (l.tenant_id,l.product_id)=(p.tenant_id,p.id) AND l.business_use
              AND c.state='VERIFIED' AND c.version=l.certificate_version
              AND (c.validity_mode='NOT_STATED' OR (c.validity_mode='FIXED'
                AND (statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
                    BETWEEN c.valid_from AND c.valid_until))) END;
      $$;
      GRANT EXECUTE ON FUNCTION med.product_qualification_current(med.product) TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
