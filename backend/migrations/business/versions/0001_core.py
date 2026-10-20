from pathlib import Path

from alembic import op

revision = "b0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = (Path(__file__).parents[1] / "sql/0001_core.sql").read_text(encoding="utf-8")
    # The reviewed SQL contains PL/pgSQL bodies; let psycopg execute the whole script.
    op.get_bind().exec_driver_sql(sql)
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA med TO med_runtime")
    op.execute("REVOKE ALL ON med.alembic_version FROM med_runtime")
    op.execute("""REVOKE UPDATE ON med.posting_batch, med.posting_line, med.stock_ledger,
        med.cost_ledger, med.outbox_event, med.trace_event, med.audit_event FROM med_runtime""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
