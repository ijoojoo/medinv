from dataclasses import dataclass
from uuid import UUID

from medinv.platform.errors import BusinessError


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID
    principal_id: UUID
    request_id: UUID
    context_generation: int
    permission_version: int
    entity_ids: frozenset[UUID]
    permissions: frozenset[str]

    def require(self, permission: str, entity_id: UUID | None = None) -> None:
        if permission not in self.permissions:
            raise BusinessError("FORBIDDEN", "没有执行此操作的权限", 403)
        if entity_id is not None and entity_id not in self.entity_ids:
            raise BusinessError("NOT_FOUND", "主体不可见或不存在", 404)
