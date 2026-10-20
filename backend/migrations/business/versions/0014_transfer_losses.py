from alembic import op

revision = "b0014"
down_revision = "b0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DO $$ DECLARE c record; BEGIN
      FOR c IN SELECT conname FROM pg_constraint
        WHERE conrelid='med.transfer'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%state%'
      LOOP EXECUTE format('ALTER TABLE med.transfer DROP CONSTRAINT %I',c.conname); END LOOP;
    END $$;
    ALTER TABLE med.transfer ADD CONSTRAINT transfer_state_allowed CHECK(state IN
      ('DRAFT','APPROVED','IN_TRANSIT','PART_RECEIVED','RECEIVED','CLOSED_WITH_LOSS','CANCELLED'));
    ALTER TABLE med.transfer ADD CONSTRAINT transfer_dispatched_state CHECK(
      (state IN ('IN_TRANSIT','PART_RECEIVED','RECEIVED','CLOSED_WITH_LOSS'))=
      (dispatch_posting_id IS NOT NULL));
    ALTER TABLE med.transfer ADD CONSTRAINT transfer_independent_review CHECK(
      state NOT IN ('APPROVED','IN_TRANSIT','PART_RECEIVED','RECEIVED','CLOSED_WITH_LOSS') OR
      (reviewed_by IS NOT NULL AND reviewed_by<>created_by));

    CREATE TABLE med.transfer_loss (
      tenant_id uuid NOT NULL,id uuid NOT NULL,transfer_id uuid NOT NULL,line_id uuid NOT NULL,
      number varchar(60) NOT NULL,qty numeric(20,6) NOT NULL CHECK(qty>0),
      lot_no varchar(200) NOT NULL,reason varchar(200) NOT NULL,evidence varchar(200) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'DRAFT'
        CHECK(state IN ('DRAFT','APPROVED','POSTED','CANCELLED')),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      reviewed_by uuid,reviewed_at timestamptz,review_note varchar(200),
      posting_id uuid,cancel_note varchar(200),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),UNIQUE(tenant_id,posting_id),
      FOREIGN KEY(tenant_id,line_id,transfer_id)
        REFERENCES med.transfer_line(tenant_id,id,transfer_id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id),
      CHECK((state='POSTED')=(posting_id IS NOT NULL)),
      CHECK(state NOT IN ('APPROVED','POSTED') OR
        (reviewed_by IS NOT NULL AND reviewed_by<>created_by AND review_note IS NOT NULL))
    );
    CREATE UNIQUE INDEX transfer_loss_unfinished ON med.transfer_loss(tenant_id,line_id)
      WHERE state IN ('DRAFT','APPROVED');
    CREATE INDEX transfer_loss_history
      ON med.transfer_loss(tenant_id,transfer_id,created_at DESC,id);
    CREATE INDEX transfer_loss_posted ON med.transfer_loss(tenant_id,line_id) WHERE state='POSTED';
    CREATE FUNCTION med.protect_transfer_loss() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.state IN ('POSTED','CANCELLED') OR
        ROW(NEW.tenant_id,NEW.id,NEW.transfer_id,NEW.line_id,NEW.number,NEW.qty,NEW.lot_no,
            NEW.reason,NEW.evidence,NEW.created_by,NEW.created_at) IS DISTINCT FROM
        ROW(OLD.tenant_id,OLD.id,OLD.transfer_id,OLD.line_id,OLD.number,OLD.qty,OLD.lot_no,
            OLD.reason,OLD.evidence,OLD.created_by,OLD.created_at) THEN
        RAISE EXCEPTION 'transfer loss application facts are immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_facts BEFORE UPDATE ON med.transfer_loss
      FOR EACH ROW EXECUTE FUNCTION med.protect_transfer_loss();
    ALTER TABLE med.transfer_loss ENABLE ROW LEVEL SECURITY;
    ALTER TABLE med.transfer_loss FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_scope ON med.transfer_loss
      USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
    GRANT SELECT,INSERT,UPDATE ON med.transfer_loss TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
