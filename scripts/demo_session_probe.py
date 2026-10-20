"""Test-only DB probe: expire/inspect an existing local demo-owner session.

No HTTP endpoint. Requires migration credentials. Never returns token plaintext.
"""
import json
import os
import re
import sys
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import create_engine, text
from dev import environment
from medinv.platform.auth.crypto import TokenCipher

command = json.load(sys.stdin)
token_hash = command["token_hash"]
if not isinstance(token_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", token_hash):
    raise SystemExit("Invalid session digest")
env = environment()
engine = create_engine(env["MED_CONTROL_MIGRATION_URL"], hide_parameters=True)
cipher = TokenCipher(env["MED_SESSION_ENCRYPTION_KEY"])
try:
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE control_owner"))
        row = conn.execute(text("""SELECT token_version,sealed_tokens FROM control.web_session
          WHERE token_hash=:h AND principal_id=:p AND revoked_at IS NULL FOR UPDATE"""),
          {"h":token_hash,"p":uuid5(NAMESPACE_URL,"medinv-dev/demo.owner")}).mappings().one()
        result = {"version":row["token_version"],"refresh_digest":sha256(cipher.open(row["sealed_tokens"])["refresh_token"].encode()).hexdigest()}
        if command.get("expire") is True:
            conn.execute(text("""UPDATE control.web_session SET token_expires_at=clock_timestamp()-interval '1 second'
              WHERE token_hash=:h"""),{"h":token_hash})
    print(json.dumps(result))
finally:
    engine.dispose()
