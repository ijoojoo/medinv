import json
import random
import time
from collections.abc import Callable
from decimal import localcontext
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError

from medinv.platform.database.sql import SqlSession
from medinv.platform.database.uow import TenantUnitOfWork
from medinv.platform.errors import BusinessError
from medinv.platform.tenancy.context import TenantContext


def fingerprint(value: dict[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command(
    uow: TenantUnitOfWork,
    ctx: TenantContext,
    name: str,
    target: str,
    key: str,
    body: dict[str, Any],
    handler: Callable[[SqlSession], dict[str, Any]],
    operation_id: UUID | None = None,
) -> dict[str, Any]:
    if not 16 <= len(key) <= 128 or not key.isascii() or any(c.isspace() for c in key):
        raise BusinessError("INVALID_IDEMPOTENCY_KEY", "幂等键格式不正确", 400)
    request_hash = fingerprint(body)
    params = {
        "tenant": ctx.tenant_id,
        "actor": ctx.principal_id,
        "name": name,
        "target": target,
        "key_hash": sha256(key.encode()).hexdigest(),
        "request_hash": request_hash,
        "id": uuid4(),
    }
    for attempt in range(3):
        try:
            with uow.open(ctx) as session:
                repo = SqlSession(session)
                # Duplicate-operation serialization precedes all business locks.
                if operation_id is not None:
                    mutex = int.from_bytes(
                        sha256(f"{ctx.tenant_id}:{operation_id}".encode()).digest()[:8], signed=True
                    )
                    repo.execute("SELECT pg_advisory_xact_lock(:mutex)", {"mutex": mutex})
                inserted = repo.rows(
                    """
                    INSERT INTO med.idempotency_record
                      (tenant_id,id,created_by,updated_by,principal_id,command,target,
                       key_hash,request_hash,state,expires_at)
                    VALUES (:tenant,:id,:actor,:actor,CAST(:actor AS text),:name,:target,
                       :key_hash,:request_hash,'IN_PROGRESS',now()+interval '7 days')
                    ON CONFLICT (tenant_id,principal_id,command,target,key_hash) DO NOTHING
                    RETURNING id
                """,
                    params,
                )
                if not inserted:
                    prior = repo.rows(
                        """
                        SELECT * FROM med.idempotency_record
                        WHERE principal_id=CAST(:actor AS text) AND command=:name
                          AND target=:target AND key_hash=:key_hash FOR UPDATE
                    """,
                        params,
                    )[0]
                    if prior["request_hash"] != request_hash:
                        raise BusinessError("IDEMPOTENCY_CONFLICT", "同一幂等键的请求内容不一致")
                    if prior["state"] != "SUCCEEDED":
                        raise BusinessError(
                            "OPERATION_PENDING", "操作尚未完成", 503, retryable=True
                        )
                    return dict(prior["response_json"])
                with localcontext() as decimal_ctx:
                    decimal_ctx.prec = 50
                    result = handler(repo)
                repo.execute(
                    """
                    UPDATE med.idempotency_record SET state='SUCCEEDED', http_status=200,
                      response_json=CAST(:result AS jsonb), updated_at=clock_timestamp()
                    WHERE id=:id
                """,
                    {**params, "result": json.dumps(result)},
                )
            return result
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "PZ001":
                raise BusinessError(
                    "COUNT_FROZEN", "该主体批次正在盘点，完成或取消盘点后方可出入库或预占", 409
                ) from exc
            if sqlstate in {"40P01", "40001"} and attempt < 2:
                time.sleep(random.uniform(0.02, 0.06) * (attempt + 1))
                continue
            if sqlstate in {"40P01", "40001", "55P03", "57014"}:
                raise BusinessError(
                    "DATABASE_BUSY", "系统繁忙，请使用原幂等键重试", 503, retryable=True
                ) from exc
            if exc.connection_invalidated:
                raise BusinessError(
                    "OPERATION_RESULT_UNKNOWN",
                    "结果待确认，请使用原幂等键重试",
                    503,
                    retryable=True,
                ) from exc
            raise
    raise AssertionError("unreachable")
