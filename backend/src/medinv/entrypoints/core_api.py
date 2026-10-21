import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from medinv.modules.catalog.api.admission import AdmissionPreview, AdmissionReview, AdmissionSave
from medinv.modules.catalog.api.admission import Channel as AdmissionChannel
from medinv.modules.catalog.api.certificates import (
    CertificateCreate,
    CertificateReview,
    CertificateRevision,
)
from medinv.modules.catalog.api.code_rules import CodeCategory, ProductCodeRuleUpdate
from medinv.modules.catalog.api.codes import (
    CatalogCodeCreate,
    CatalogCodeState,
    CodeOwner,
    CodeScheme,
)
from medinv.modules.catalog.api.dictionaries import DictionaryKey, DictionaryUpdate
from medinv.modules.catalog.api.field_rules import Category, FieldRulesUpdate
from medinv.modules.catalog.api.imports import ProductImport
from medinv.modules.catalog.api.master import (
    EntitySettingsSave,
    MasterImport,
    MasterSave,
    OrganizationCreate,
    SchemaPublish,
    SchemaSave,
)
from medinv.modules.catalog.api.operations import MasterKind, OperationsUpdate
from medinv.modules.catalog.api.packaging import PackagingCreate
from medinv.modules.catalog.api.party_certificates import (
    PartyCertificateCreate,
    PartyCertificateReview,
    PartyCertificateRevision,
)
from medinv.modules.catalog.api.prices import ReferencePricesUpdate
from medinv.modules.catalog.api.qualification import BundleDiscard, RulesSave, ScopeSave
from medinv.modules.catalog.api.schemas import (
    PartyCreate,
    PartyRevision,
    ProductCreate,
    ProductRevision,
    Review,
)
from medinv.modules.catalog.application.admission import AdmissionPolicies
from medinv.modules.catalog.application.attachments import MAX_BYTES, CatalogAttachments
from medinv.modules.catalog.application.certificates import ProductCertificates
from medinv.modules.catalog.application.change_bundle import ChangeBundles
from medinv.modules.catalog.application.code_rules import ProductCodeRules
from medinv.modules.catalog.application.codes import CatalogCodes
from medinv.modules.catalog.application.dictionaries import CatalogDictionaries
from medinv.modules.catalog.application.entity_settings import ProductEntitySettings
from medinv.modules.catalog.application.entry import EntryProducts
from medinv.modules.catalog.application.field_rules import ProductFieldRules
from medinv.modules.catalog.application.imports import ProductImports
from medinv.modules.catalog.application.maintenance import CatalogMaintenance, Kind
from medinv.modules.catalog.application.master import ProductMaster
from medinv.modules.catalog.application.master_config import MasterConfiguration
from medinv.modules.catalog.application.operations import CatalogOperations
from medinv.modules.catalog.application.packaging import PackagingService
from medinv.modules.catalog.application.party_certificates import PartyCertificates
from medinv.modules.catalog.application.party_lookup import PartyLookup
from medinv.modules.catalog.application.prices import ReferencePrices
from medinv.modules.catalog.application.qualification_rules import QualificationRules
from medinv.modules.catalog.application.scoped_admission import PartyScopes
from medinv.modules.catalog.application.service import CatalogService
from medinv.modules.inventory.api.counts import CountCreate, CountReview, CountSubmit
from medinv.modules.inventory.api.location_moves import (
    LocationMoveCreate,
    LocationMovePost,
    LocationMoveReview,
)
from medinv.modules.inventory.api.openings import OpeningCreate, OpeningReview
from medinv.modules.inventory.api.quality import DispositionCreate, DispositionReview
from medinv.modules.inventory.api.schemas import ReleaseRequest, ReserveRequest
from medinv.modules.inventory.api.transfer_losses import TransferLossCreate, TransferLossReview
from medinv.modules.inventory.api.transfers import (
    TransferCreate,
    TransferReceive,
    TransferReview,
    WarehouseCreate,
)
from medinv.modules.inventory.api.warehouses import LocationCreate, WarehouseRename
from medinv.modules.inventory.application import (
    count_posting,
    location_move_posting,
    opening_posting,
    quality_posting,
    sale_stops,
    transfer_posting,
)
from medinv.modules.inventory.application.counts import CountService
from medinv.modules.inventory.application.location_moves import LocationMoveService
from medinv.modules.inventory.application.openings import OpeningService
from medinv.modules.inventory.application.quality import QualityService
from medinv.modules.inventory.application.service import InventoryService
from medinv.modules.inventory.application.transfer_losses import TransferLossService
from medinv.modules.inventory.application.transfers import TransferService
from medinv.modules.inventory.application.warehouse_lookup import WarehouseLookup
from medinv.modules.inventory.application.warehouses import WarehouseService
from medinv.modules.procurement.api.schemas import (
    Approval,
    InspectionCreate,
    OrderCreate,
    OrderRevision,
    ReceiptCreate,
    ReceiptPost,
    RejectedHandover,
    Transition,
)
from medinv.modules.procurement.application import amendments as purchase_amendments
from medinv.modules.procurement.application.conclusions import options as purchase_review_options
from medinv.modules.procurement.application.rejected_handover import hand_back
from medinv.modules.procurement.application.service import ProcurementService
from medinv.modules.retail.api.schemas import (
    RetailCashConfirmation,
    RetailComplete,
    RetailCreate,
    RetailPayment,
)
from medinv.modules.retail.application import payments as retail_payments
from medinv.modules.retail.application import posting as retail_posting
from medinv.modules.retail.application.service import RetailService
from medinv.modules.returns.api.schemas import (
    CustodyHandover,
    RefundCreate,
    ReturnCreate,
    ReturnReceive,
    ReturnReview,
)
from medinv.modules.returns.application import custody, refunds, retail_refunds
from medinv.modules.returns.application import posting as return_posting
from medinv.modules.returns.application.service import ReturnService
from medinv.modules.sales.api.schemas import (
    CreditSet,
    Pick,
    ReplenishmentCreate,
    SalesCreate,
    SalesRevision,
    ShipmentCreate,
    ShipmentPost,
    Verify,
)
from medinv.modules.sales.application import amendments as sales_amendments
from medinv.modules.sales.application import fulfillment
from medinv.modules.sales.application import posting as sales_posting
from medinv.modules.sales.application.replenishment import ReplenishmentService
from medinv.modules.sales.application.service import SalesService
from medinv.modules.sales.application.stock import SaleStockQueries
from medinv.modules.settlement.api.schemas import (
    AccountCreate,
    AllocateRequest,
    CashCreate,
    ReverseRequest,
)
from medinv.modules.settlement.application import allocation as settlement_allocation
from medinv.modules.settlement.application.service import SettlementService
from medinv.modules.workspace.application.dashboard import WorkDashboard
from medinv.modules.workspace.application.queue import WorkKind, WorkQueue
from medinv.modules.workspace.approvals import ApprovalWorkflows, PolicyPublish
from medinv.modules.workspace.document_columns import ColumnSave as DocumentColumnSave
from medinv.modules.workspace.drafts import (
    Channel,
    DocumentDrafts,
    DraftChannel,
    DraftDiscard,
    DraftSave,
)
from medinv.modules.workspace.management import Kind as ManagementKind
from medinv.modules.workspace.management import Management, ManagementSave, MessageRead
from medinv.modules.workspace.quality_records import QualityRecordQuery, QualityRecords
from medinv.modules.workspace.reminder_sources import Source as ReminderSource
from medinv.modules.workspace.reminders import ReminderAction, Reminders
from medinv.modules.workspace.templates import EntryTemplates, TemplateSave
from medinv.platform.auth.sessions import SessionStore
from medinv.platform.config import Settings
from medinv.platform.database.uow import TenantUnitOfWork, check_runtime_role, make_engine
from medinv.platform.errors import BusinessError
from medinv.platform.openapi import install_contract
from medinv.platform.tenancy.context import TenantContext

logger = logging.getLogger("medinv")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    business = make_engine(settings.database_url.get_secret_value(), settings)
    control = make_engine(settings.control_database_url.get_secret_value(), settings)
    inventory = InventoryService(TenantUnitOfWork(business, settings))
    sessions = SessionStore(control)
    catalog = CatalogService(TenantUnitOfWork(business, settings))
    catalog_maintenance = CatalogMaintenance(catalog.uow)
    packaging = PackagingService(catalog.uow)
    procurement = ProcurementService(TenantUnitOfWork(business, settings))
    sales = SalesService(TenantUnitOfWork(business, settings))
    sale_stock = SaleStockQueries(inventory.uow)
    replenishment = ReplenishmentService(inventory.uow)
    settlement = SettlementService(TenantUnitOfWork(business, settings))
    returns = ReturnService(TenantUnitOfWork(business, settings))
    retail = RetailService(TenantUnitOfWork(business, settings))
    counts = CountService(TenantUnitOfWork(business, settings))
    openings = OpeningService(TenantUnitOfWork(business, settings))
    quality = QualityService(TenantUnitOfWork(business, settings))
    transfers = TransferService(TenantUnitOfWork(business, settings))
    location_moves = LocationMoveService(transfers.uow)
    transfer_losses = TransferLossService(transfers)
    warehouses = WarehouseService(transfers.uow)
    work_queue = WorkQueue(inventory.uow)
    dashboard = WorkDashboard(inventory.uow)
    drafts = DocumentDrafts(inventory.uow)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await anyio.to_thread.run_sync(check_runtime_role, business, "med")
            await anyio.to_thread.run_sync(check_runtime_role, control, "control")
            yield
        finally:
            business.dispose()
            control.dispose()

    app = FastAPI(title="药械进销存 · 核心 API", version="0.34.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def error_response(request: Request, error: BusinessError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
                "request_id": str(request.state.request_id),
            },
        )

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo request values, cookies or Pydantic's raw input in error payloads.
        return error_response(
            request, BusinessError("VALIDATION_ERROR", "请求字段或格式不正确", 422)
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(
            request, BusinessError("HTTP_ERROR", "请求路径或方法不支持", exc.status_code)
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error(
            "database_failure request_id=%s class=%s", request.state.request_id, type(exc).__name__
        )
        return error_response(
            request, BusinessError("DATABASE_UNAVAILABLE", "数据库暂不可用", 503, retryable=True)
        )

    def auth(request: Request, *, write: bool = False, context: bool = False) -> TenantContext:
        if write and request.headers.get("Origin") != settings.public_origin:
            raise BusinessError("ORIGIN_REJECTED", "请求来源校验失败", 403)
        return sessions.authenticate(
            request.cookies.get("MED_SESSION"),
            request.state.request_id,
            request.headers.get("X-Context-Generation"),
            request.headers.get("X-CSRF-Token"),
            write=write,
            context_endpoint=context,
        )

    def envelope(request: Request, data: dict[str, Any]) -> dict[str, Any]:
        return {"data": data, "request_id": str(request.state.request_id)}

    def command_key(request: Request) -> str:
        if not settings.inventory_commands_enabled:
            raise BusinessError("FEATURE_NOT_ENABLED", "库存命令尚未对业务环境开放", 503)
        return request.headers.get("Idempotency-Key", "")

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> dict[str, str]:
        for engine in (business, control):
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/v1/context", operation_id="getContext")
    def context(request: Request) -> dict[str, Any]:
        ctx = auth(request, context=True)
        return envelope(
            request,
            {
                "tenant_id": str(ctx.tenant_id),
                "subject_id": str(ctx.principal_id),
                "context_generation": str(ctx.context_generation),
                "permission_version": str(ctx.permission_version),
                "entity_ids": sorted(map(str, ctx.entity_ids)),
                "permissions": sorted(ctx.permissions),
            },
        )

    @app.get("/api/v1/inventory/options", operation_id="getInventoryOptions")
    def inventory_options(
        request: Request, entity_id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, inventory.options(auth(request), entity_id, offset))

    @app.get("/api/v1/work-queue")
    def work_queue_items(
        request: Request,
        entity_id: UUID,
        kind: WorkKind = WorkKind.ALL,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=50, ge=1, le=50),
        priority: bool = False,
    ) -> dict[str, Any]:
        return envelope(
            request,
            work_queue.listing(
                auth(request), entity_id, kind, search, offset, limit=limit, priority=priority
            ),
        )

    @app.get("/api/v1/workbench/period")
    def workbench_period(
        request: Request, entity_id: UUID, days: Literal["7", "30"] = "7"
    ) -> dict[str, Any]:
        return envelope(request, dashboard.period(auth(request), entity_id, int(days)))

    @app.get("/api/v1/workbench/snapshot")
    def workbench_snapshot(request: Request, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, dashboard.snapshot(auth(request), entity_id))

    @app.get("/api/v1/workbench/attention")
    def workbench_attention(
        request: Request,
        entity_id: UUID,
        kind: Literal["overdue", "near", "expired"],
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(request, dashboard.attention(auth(request), entity_id, kind, offset))

    @app.get("/api/v1/workbench/risks")
    def workbench_risks(
        request: Request,
        entity_id: UUID,
        kind: WorkKind = WorkKind.ALL,
        group: Literal["all", "quality", "receipt", "returns", "transit"] = "all",
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, dashboard.risks(auth(request), entity_id, kind, search, offset, group)
        )

    @app.get("/api/v1/inventory/availability", operation_id="getAvailability")
    def availability(
        request: Request, entity_id: UUID, warehouse_id: UUID, sku_id: UUID
    ) -> dict[str, Any]:
        ctx = auth(request)
        return envelope(request, inventory.availability(ctx, entity_id, warehouse_id, sku_id))

    @app.post("/api/v1/reservations", operation_id="reserveStock")
    def reserve(request: Request, body: ReserveRequest) -> dict[str, Any]:
        ctx = auth(request, write=True)
        return envelope(request, inventory.reserve(ctx, body, command_key(request)))

    @app.post("/api/v1/reservations/{id}/release", operation_id="releaseReservation")
    def release(request: Request, id: UUID, body: ReleaseRequest) -> dict[str, Any]:
        ctx = auth(request, write=True)
        return envelope(request, inventory.release(ctx, id, body, command_key(request)))

    def guarded(
        request: Request,
        *,
        sales_write: bool = False,
        settlement_write: bool = False,
        returns_write: bool = False,
        retail_write: bool = False,
        count_write: bool = False,
        transfer_write: bool = False,
        quality_write: bool = False,
        warehouse_write: bool = False,
        opening_write: bool = False,
    ) -> Any:
        enabled = (
            settings.sales_commands_enabled
            if sales_write
            else settings.procurement_commands_enabled
        )
        if settlement_write:
            enabled = settings.settlement_commands_enabled
        if returns_write:
            enabled = settings.returns_commands_enabled
        if retail_write:
            enabled = settings.retail_commands_enabled
        if count_write:
            enabled = settings.count_commands_enabled
        if quality_write:
            enabled = settings.quality_commands_enabled
        if transfer_write:
            enabled = settings.transfer_commands_enabled
        if warehouse_write:
            enabled = settings.warehouse_commands_enabled
        if opening_write:
            enabled = settings.opening_commands_enabled
        if not enabled:
            raise BusinessError("FEATURE_NOT_ENABLED", "该业务写入尚未开放", 503)
        if request.headers.get("Origin") != settings.public_origin:
            raise BusinessError("ORIGIN_REJECTED", "请求来源校验失败", 403)
        return sessions.guard(
            request.cookies.get("MED_SESSION"),
            request.state.request_id,
            request.headers.get("X-Context-Generation"),
            request.headers.get("X-CSRF-Token"),
        )

    def key(request: Request) -> str:
        return request.headers.get("Idempotency-Key", "")

    @app.get("/api/v1/catalog/skus/{id}/packaging")
    def packaging_list(
        request: Request,
        id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        approved_only: bool = False,
    ) -> dict[str, Any]:
        return envelope(request, packaging.listing(auth(request), id, offset, approved_only))

    @app.post("/api/v1/catalog/skus/{id}/packaging")
    def packaging_create(request: Request, id: UUID, body: PackagingCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, packaging.create(ctx, id, body, key(request)))

    @app.post("/api/v1/catalog/skus/{id}/packaging/{packaging_id}/review")
    def packaging_review(
        request: Request, id: UUID, packaging_id: UUID, body: Review
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, packaging.review(ctx, id, packaging_id, body, key(request)))

    @app.get("/api/v1/location-moves/candidates")
    def move_candidates(
        request: Request,
        entity_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, location_moves.candidates(auth(request), entity_id, search, offset)
        )

    @app.get("/api/v1/location-moves/destinations")
    def move_destinations(
        request: Request,
        entity_id: UUID,
        balance_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, location_moves.destinations(auth(request), entity_id, balance_id, offset)
        )

    @app.get("/api/v1/location-moves")
    def move_list(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=30),
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            location_moves.listing(
                auth(request),
                entity_id,
                offset,
                search=search,
                state=state,
                date_from=date_from,
                date_to=date_to,
            ),
        )

    @app.post("/api/v1/location-moves")
    def move_create(request: Request, body: LocationMoveCreate) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, location_moves.create(ctx, body, key(request)))

    @app.get("/api/v1/location-moves/{id}")
    def move_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, location_moves.detail(auth(request), id))

    @app.post("/api/v1/location-moves/{id}/review")
    def move_review(request: Request, id: UUID, body: LocationMoveReview) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, location_moves.transition(ctx, id, body, key(request), True))

    @app.post("/api/v1/location-moves/{id}/cancel")
    def move_cancel(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, location_moves.transition(ctx, id, body, key(request)))

    @app.post("/api/v1/location-moves/{id}/post")
    def move_post(request: Request, id: UUID, body: LocationMovePost) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(
                request, location_move_posting.post(location_moves, ctx, id, body, key(request))
            )

    @app.get("/api/v1/quality-dispositions/candidates")
    def quality_candidates(
        request: Request,
        entity_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(request, quality.candidates(auth(request), entity_id, search, offset))

    @app.get("/api/v1/quality-dispositions")
    def quality_list(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=30),
        date_from: date | None = None,
        date_to: date | None = None,
        kind: str = Query(default="", max_length=30),
    ) -> dict[str, Any]:
        return envelope(
            request,
            quality.listing(
                auth(request),
                entity_id,
                offset,
                search=search,
                state=state,
                date_from=date_from,
                date_to=date_to,
                kind=kind,
            ),
        )

    @app.post("/api/v1/quality-dispositions")
    def quality_create(request: Request, body: DispositionCreate) -> dict[str, Any]:
        with guarded(request, quality_write=True) as ctx:
            return envelope(request, quality.create(ctx, body, key(request)))

    @app.get("/api/v1/quality-dispositions/{id}")
    def quality_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, quality.detail(auth(request), id))

    @app.post("/api/v1/quality-dispositions/{id}/review")
    def quality_review(request: Request, id: UUID, body: DispositionReview) -> dict[str, Any]:
        with guarded(request, quality_write=True) as ctx:
            return envelope(request, quality.transition(ctx, id, body, key(request), True))

    @app.post("/api/v1/quality-dispositions/{id}/cancel")
    def quality_cancel(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, quality_write=True) as ctx:
            return envelope(request, quality.transition(ctx, id, body, key(request)))

    @app.post("/api/v1/quality-dispositions/{id}/post")
    def quality_post(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, quality_write=True) as ctx:
            return envelope(request, quality_posting.post(quality, ctx, id, body, key(request)))

    @app.get("/api/v1/transfers/{id}/losses")
    def transfer_loss_list(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, transfer_losses.listing(auth(request), id, offset))

    @app.post("/api/v1/transfers/{id}/losses")
    def transfer_loss_create(
        request: Request, id: UUID, body: TransferLossCreate
    ) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, transfer_losses.create(ctx, id, body, key(request)))

    @app.get("/api/v1/transfers/{id}/losses/{loss_id}")
    def transfer_loss_detail(request: Request, id: UUID, loss_id: UUID) -> dict[str, Any]:
        return envelope(request, transfer_losses.detail(auth(request), id, loss_id))

    @app.post("/api/v1/transfers/{id}/losses/{loss_id}/review")
    def transfer_loss_review(
        request: Request, id: UUID, loss_id: UUID, body: TransferLossReview
    ) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(
                request, transfer_losses.transition(ctx, id, loss_id, body, key(request), True)
            )

    @app.post("/api/v1/transfers/{id}/losses/{loss_id}/cancel")
    def transfer_loss_cancel(
        request: Request, id: UUID, loss_id: UUID, body: Transition
    ) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(
                request, transfer_losses.transition(ctx, id, loss_id, body, key(request))
            )

    @app.post("/api/v1/transfers/{id}/losses/{loss_id}/post")
    def transfer_loss_post(
        request: Request, id: UUID, loss_id: UUID, body: Transition
    ) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, transfer_losses.post(ctx, id, loss_id, body, key(request)))

    @app.get("/api/v1/transfers/options")
    def transfer_options(request: Request, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, transfers.options(auth(request), entity_id))

    @app.post("/api/v1/warehouses")
    def warehouse_create(request: Request, body: WarehouseCreate) -> dict[str, Any]:
        with guarded(request, warehouse_write=True) as ctx:
            return envelope(request, warehouses.create(ctx, body, key(request)))

    @app.get("/api/v1/warehouses")
    def warehouse_list(
        request: Request,
        entity_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(request, warehouses.listing(auth(request), entity_id, search, offset))

    @app.get("/api/v1/warehouses/lookup")
    def warehouse_lookup(
        request: Request,
        entity_id: UUID,
        channel: Literal["purchase", "sale"],
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
        warehouse_id: UUID | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            WarehouseLookup(catalog.uow).search(
                auth(request), entity_id, channel, search, offset, warehouse_id
            ),
        )

    @app.get("/api/v1/warehouses/{id}")
    def warehouse_detail(
        request: Request,
        id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
        history_offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, warehouses.detail(auth(request), id, search, offset, history_offset)
        )

    @app.post("/api/v1/warehouses/{id}/rename")
    def warehouse_rename(request: Request, id: UUID, body: WarehouseRename) -> dict[str, Any]:
        with guarded(request, warehouse_write=True) as ctx:
            return envelope(request, warehouses.rename(ctx, id, body, key(request)))

    @app.post("/api/v1/warehouses/{id}/locations")
    def location_create(request: Request, id: UUID, body: LocationCreate) -> dict[str, Any]:
        with guarded(request, warehouse_write=True) as ctx:
            return envelope(request, warehouses.add_location(ctx, id, body, key(request)))

    @app.post("/api/v1/warehouses/{id}/locations/{location_id}/rename")
    def location_rename(
        request: Request,
        id: UUID,
        location_id: UUID,
        body: WarehouseRename,
    ) -> dict[str, Any]:
        with guarded(request, warehouse_write=True) as ctx:
            return envelope(request, warehouses.rename(ctx, id, body, key(request), location_id))

    @app.get("/api/v1/transfers/candidates")
    def transfer_candidates(
        request: Request,
        entity_id: UUID,
        warehouse_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, transfers.candidates(auth(request), entity_id, warehouse_id, search, offset)
        )

    @app.get("/api/v1/transfers")
    def transfer_list(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=30),
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            transfers.listing(
                auth(request),
                entity_id,
                offset,
                search=search,
                state=state,
                date_from=date_from,
                date_to=date_to,
            ),
        )

    @app.post("/api/v1/transfers")
    def transfer_create(request: Request, body: TransferCreate) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, transfers.create(ctx, body, key(request)))

    @app.get("/api/v1/transfers/{id}")
    def transfer_detail(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, transfers.detail(auth(request), id, offset))

    @app.post("/api/v1/transfers/{id}/review")
    def transfer_review(request: Request, id: UUID, body: TransferReview) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, transfers.transition(ctx, id, body, key(request), True))

    @app.post("/api/v1/transfers/{id}/cancel")
    def transfer_cancel(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(request, transfers.transition(ctx, id, body, key(request)))

    @app.post("/api/v1/transfers/{id}/dispatch")
    def transfer_dispatch(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(
                request, transfer_posting.dispatch(transfers, ctx, id, body, key(request))
            )

    @app.post("/api/v1/transfers/{id}/receive")
    def transfer_receive(request: Request, id: UUID, body: TransferReceive) -> dict[str, Any]:
        with guarded(request, transfer_write=True) as ctx:
            return envelope(
                request, transfer_posting.receive(transfers, ctx, id, body, key(request))
            )

    @app.get("/api/v1/inventory-openings/options")
    def opening_options(
        request: Request, entity_id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, openings.options(auth(request), entity_id, offset))

    @app.get("/api/v1/inventory-openings")
    def opening_list(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=30),
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            openings.listing(
                auth(request),
                entity_id,
                offset,
                search=search,
                state=state,
                date_from=date_from,
                date_to=date_to,
            ),
        )

    @app.post("/api/v1/inventory-openings")
    def opening_create(request: Request, body: OpeningCreate) -> dict[str, Any]:
        with guarded(request, opening_write=True) as ctx:
            return envelope(request, openings.create(ctx, body, key(request)))

    @app.get("/api/v1/inventory-openings/{id}")
    def opening_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, openings.detail(auth(request), id))

    @app.post("/api/v1/inventory-openings/{id}/review")
    def opening_review(request: Request, id: UUID, body: OpeningReview) -> dict[str, Any]:
        with guarded(request, opening_write=True) as ctx:
            return envelope(request, openings.transition(ctx, id, body, key(request), True))

    @app.post("/api/v1/inventory-openings/{id}/cancel")
    def opening_cancel(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, opening_write=True) as ctx:
            return envelope(request, openings.transition(ctx, id, body, key(request)))

    @app.post("/api/v1/inventory-openings/{id}/post")
    def opening_post(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, opening_write=True) as ctx:
            return envelope(request, opening_posting.post(openings, ctx, id, body, key(request)))

    @app.get("/api/v1/stock-counts/options")
    def count_options(request: Request, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, counts.options(auth(request), entity_id))

    @app.get("/api/v1/stock-counts/candidates")
    def count_candidates(
        request: Request,
        entity_id: UUID,
        warehouse_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, counts.candidates(auth(request), entity_id, warehouse_id, search, offset)
        )

    @app.get("/api/v1/stock-counts")
    def count_list(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=30),
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            counts.listing(
                auth(request),
                entity_id,
                offset,
                search=search,
                state=state,
                date_from=date_from,
                date_to=date_to,
            ),
        )

    @app.post("/api/v1/stock-counts")
    def count_create(request: Request, body: CountCreate) -> dict[str, Any]:
        with guarded(request, count_write=True) as ctx:
            return envelope(request, counts.create(ctx, body, key(request)))

    @app.get("/api/v1/stock-counts/{id}")
    def count_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, counts.detail(auth(request), id))

    @app.post("/api/v1/stock-counts/{id}/submit")
    def count_submit(request: Request, id: UUID, body: CountSubmit) -> dict[str, Any]:
        with guarded(request, count_write=True) as ctx:
            return envelope(request, counts.submit(ctx, id, body, key(request)))

    @app.post("/api/v1/stock-counts/{id}/review")
    def count_review(request: Request, id: UUID, body: CountReview) -> dict[str, Any]:
        with guarded(request, count_write=True) as ctx:
            return envelope(request, counts.review(ctx, id, body, key(request)))

    @app.post("/api/v1/stock-counts/{id}/post")
    def count_post(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, count_write=True) as ctx:
            return envelope(request, count_posting.post(counts, ctx, id, body, key(request)))

    @app.post("/api/v1/stock-counts/{id}/cancel")
    def count_cancel(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, count_write=True) as ctx:
            return envelope(request, counts.cancel(ctx, id, body, key(request)))

    @app.get("/api/v1/document-columns/{stage}/{mode}")
    def document_columns_detail(
        request: Request,
        stage: Literal["orders", "receiving", "inspection", "posting", "sales_orders"],
        mode: Literal["editor", "view"],
        entity_id: UUID,
    ) -> dict[str, Any]:
        from medinv.modules.workspace.document_columns import DocumentColumns

        return envelope(
            request, DocumentColumns(inventory.uow).detail(auth(request), entity_id, stage, mode)
        )

    @app.post("/api/v1/document-columns/{stage}/{mode}")
    def document_columns_save(
        request: Request,
        stage: Literal["orders", "receiving", "inspection", "posting", "sales_orders"],
        mode: Literal["editor", "view"],
        entity_id: UUID,
        body: DocumentColumnSave,
    ) -> dict[str, Any]:
        from medinv.modules.workspace.document_columns import DocumentColumns

        with guarded(request) as ctx:
            return envelope(
                request,
                DocumentColumns(inventory.uow).save(
                    ctx, entity_id, stage, mode, body, key(request)
                ),
            )

    @app.get("/api/v1/entry-templates/{channel}")
    def entry_template_detail(request: Request, channel: Channel) -> dict[str, Any]:
        return envelope(request, EntryTemplates(inventory.uow).detail(auth(request), channel))

    @app.post("/api/v1/entry-templates/{channel}")
    def entry_template_save(
        request: Request, channel: Channel, body: TemplateSave
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, EntryTemplates(inventory.uow).save(ctx, channel, body, key(request))
            )

    @app.get("/api/v1/document-drafts")
    def draft_list(
        request: Request,
        entity_id: UUID,
        channel: DraftChannel = "all",
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        limit: int = Query(default=50, ge=1, le=50),
    ) -> dict[str, Any]:
        return envelope(
            request,
            drafts.listing(auth(request), channel, entity_id, offset, search=search, limit=limit),
        )

    @app.get("/api/v1/document-drafts/{id}")
    def draft_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, drafts.detail(auth(request), id))

    @app.post("/api/v1/document-drafts/{id}")
    def draft_save(request: Request, id: UUID, body: DraftSave) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, drafts.save(ctx, id, body, key(request)))

    @app.post("/api/v1/document-drafts/{id}/discard")
    def draft_discard(request: Request, id: UUID, body: DraftDiscard) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, drafts.discard(ctx, id, body, key(request)))

    @app.get("/api/v1/catalog/master/custom-field")
    def master_custom_field(
        request: Request,
        key: str = Query(pattern=r"^custom_[a-z][a-z0-9_]{0,40}$"),
        label: str = Query(min_length=1, max_length=40),
        kind: Literal["text", "integer", "decimal", "boolean", "date"] = "text",
        private: bool = False,
    ) -> dict[str, Any]:
        from medinv.modules.catalog.domain.master_schema import field

        auth(request).require("catalog.configure")
        definition = field(key, label, "custom", kind, binding="custom")
        definition["lifecycle"] = "CUSTOM"
        definition["read_permission"] = "catalog.review" if private else "catalog.read"
        return envelope(request, definition)

    @app.get("/api/v1/catalog/master/schema")
    def master_schema(request: Request) -> dict[str, Any]:
        return envelope(request, MasterConfiguration(catalog.uow).get(auth(request)))

    @app.get("/api/v1/catalog/master/schema/{id}")
    def master_schema_snapshot(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, MasterConfiguration(catalog.uow).snapshot(auth(request), id))

    @app.post("/api/v1/catalog/master/schema/draft")
    def master_schema_draft(request: Request, body: SchemaSave) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, MasterConfiguration(catalog.uow).save(ctx, body, key(request)))

    @app.post("/api/v1/catalog/master/schema/{id}/publish")
    def master_schema_publish(request: Request, id: UUID, body: SchemaPublish) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, MasterConfiguration(catalog.uow).publish(ctx, id, body, key(request))
            )

    @app.get("/api/v1/catalog/master/products")
    def master_products(
        request: Request,
        node: str = "",
        search: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=50, ge=1, le=5000),
        sort: str = "code",
        descending: bool = False,
        state: Literal["", "DRAFT", "APPROVED", "SUSPENDED"] = "",
    ) -> dict[str, Any]:
        return envelope(
            request,
            ProductMaster(catalog.uow).listing(
                auth(request), node, search, offset, limit, sort, descending, state
            ),
        )

    @app.get("/api/v1/catalog/master/products/{id}")
    def master_product(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, ProductMaster(catalog.uow).detail(auth(request), id))

    @app.get("/api/v1/catalog/master/products/{id}/entity-settings")
    def master_entity_settings(request: Request, id: UUID, entity_id: UUID) -> dict[str, Any]:
        return envelope(
            request, ProductEntitySettings(catalog.uow).get(auth(request), id, entity_id)
        )

    @app.post("/api/v1/catalog/master/products/{id}/entity-settings")
    def master_entity_settings_save(
        request: Request, id: UUID, body: EntitySettingsSave
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ProductEntitySettings(catalog.uow).save(ctx, id, body, key(request))
            )

    @app.get("/api/v1/catalog/master/products/{id}/history/{version}")
    def master_product_history(request: Request, id: UUID, version: int) -> dict[str, Any]:
        return envelope(request, ProductMaster(catalog.uow).history(auth(request), id, version))

    @app.post("/api/v1/catalog/master/products")
    def master_product_save(request: Request, body: MasterSave) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, ProductMaster(catalog.uow).save(ctx, body, key(request)))

    @app.post("/api/v1/catalog/master/validate")
    def master_product_validate(request: Request, body: MasterSave) -> dict[str, Any]:
        return envelope(request, ProductMaster(catalog.uow).validate(auth(request), body))

    @app.post("/api/v1/catalog/master/import/preview")
    def master_import_preview(request: Request, body: MasterImport) -> dict[str, Any]:
        return envelope(request, ProductMaster(catalog.uow).import_csv(auth(request), body))

    @app.post("/api/v1/catalog/master/import")
    def master_import(request: Request, body: MasterImport) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, ProductMaster(catalog.uow).import_csv(ctx, body, key(request)))

    @app.get("/api/v1/catalog/master/organizations")
    def master_organizations(
        request: Request, search: str = Query(default="", max_length=200)
    ) -> dict[str, Any]:
        return envelope(request, ProductMaster(catalog.uow).organizations(auth(request), search))

    @app.post("/api/v1/catalog/master/organizations")
    def master_organization_create(request: Request, body: OrganizationCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ProductMaster(catalog.uow).create_organization(ctx, body, key(request))
            )

    @app.get("/api/v1/catalog/master/certificates")
    def master_certificates(
        request: Request,
        search: str = Query(default="", max_length=200),
        product_id: UUID | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request, ProductMaster(catalog.uow).certificates(auth(request), search, product_id)
        )

    @app.get("/api/v1/catalog/entry-parties")
    def entry_parties(
        request: Request,
        entity_id: UUID,
        channel: Literal["purchase", "sale"],
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
        party_id: UUID | None = None,
        qualified_only: bool = True,
    ) -> dict[str, Any]:
        return envelope(
            request,
            PartyLookup(catalog.uow).search(
                auth(request), entity_id, channel, search, offset, party_id, qualified_only
            ),
        )

    @app.get("/api/v1/catalog/entry-products")
    def entry_products(
        request: Request,
        entity_id: UUID,
        channel: Literal["purchase", "sale"],
        search: str = Query(default="", max_length=128),
        offset: int = Query(default=0, ge=0, le=100000),
        sku_id: UUID | None = None,
        match_barcode: bool = True,
    ) -> dict[str, Any]:
        return envelope(
            request,
            EntryProducts(catalog.uow).search(
                auth(request), entity_id, channel, search, offset, sku_id, match_barcode
            ),
        )

    @app.get("/api/v1/catalog/code-rules/{category}")
    def product_code_rules(request: Request, category: CodeCategory) -> dict[str, Any]:
        return envelope(request, ProductCodeRules(catalog.uow).get(auth(request), category))

    @app.post("/api/v1/catalog/code-rules/{category}")
    def update_product_code_rules(
        request: Request, category: CodeCategory, body: ProductCodeRuleUpdate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ProductCodeRules(catalog.uow).update(ctx, category, body, key(request))
            )

    @app.get("/api/v1/catalog/skus/{id}/prices")
    def reference_prices(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, ReferencePrices(catalog.uow).get(auth(request), id))

    @app.post("/api/v1/catalog/skus/{id}/prices")
    def update_reference_prices(
        request: Request, id: UUID, body: ReferencePricesUpdate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ReferencePrices(catalog.uow).update(ctx, id, body, key(request))
            )

    @app.get("/api/v1/catalog/field-rules/{category}")
    def product_field_rules(request: Request, category: Category) -> dict[str, Any]:
        return envelope(request, ProductFieldRules(catalog.uow).get(auth(request), category))

    @app.post("/api/v1/catalog/field-rules/{category}")
    def update_product_field_rules(
        request: Request, category: Category, body: FieldRulesUpdate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ProductFieldRules(catalog.uow).update(ctx, category, body, key(request))
            )

    @app.get("/api/v1/catalog/qualification-rules")
    def qualification_rules(request: Request) -> dict[str, Any]:
        return envelope(request, QualificationRules(catalog.uow).get(auth(request)))

    @app.post("/api/v1/catalog/qualification-rules")
    def qualification_rules_save(request: Request, body: RulesSave) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, QualificationRules(catalog.uow).save(ctx, body, key(request)))

    @app.get("/api/v1/catalog/changes/{kind}/{id}")
    def catalog_changes(request: Request, kind: Kind, id: UUID) -> dict[str, Any]:
        return envelope(request, ChangeBundles(catalog.uow).detail(auth(request), kind, id))

    @app.post("/api/v1/catalog/changes/{kind}/{id}/discard")
    def catalog_changes_discard(
        request: Request, kind: Kind, id: UUID, body: BundleDiscard
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ChangeBundles(catalog.uow).discard(ctx, kind, id, body, key(request))
            )

    @app.get("/api/v1/catalog/parties/{id}/scopes")
    def party_scopes(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, PartyScopes(catalog.uow).get(auth(request), id))

    @app.post("/api/v1/catalog/parties/{id}/scopes")
    def party_scopes_save(request: Request, id: UUID, body: ScopeSave) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, PartyScopes(catalog.uow).save(ctx, id, body, key(request)))

    @app.get("/api/v1/catalog/admission/{entity_id}/{channel}")
    def admission_detail(
        request: Request, entity_id: UUID, channel: AdmissionChannel
    ) -> dict[str, Any]:
        return envelope(
            request, AdmissionPolicies(catalog.uow).detail(auth(request), entity_id, channel)
        )

    @app.post("/api/v1/catalog/admission/{entity_id}/{channel}")
    def admission_save(
        request: Request, entity_id: UUID, channel: AdmissionChannel, body: AdmissionSave
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                AdmissionPolicies(catalog.uow).save(ctx, entity_id, channel, body, key(request)),
            )

    @app.post("/api/v1/catalog/admission/{entity_id}/{channel}/review")
    def admission_review(
        request: Request, entity_id: UUID, channel: AdmissionChannel, body: AdmissionReview
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                AdmissionPolicies(catalog.uow).review(ctx, entity_id, channel, body, key(request)),
            )

    @app.post("/api/v1/catalog/admission/{entity_id}/{channel}/preview")
    def admission_preview(
        request: Request, entity_id: UUID, channel: AdmissionChannel, body: AdmissionPreview
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, AdmissionPolicies(catalog.uow).preview(ctx, entity_id, channel, body)
            )

    @app.get("/api/v1/catalog/admission/{entity_id}/{channel}/products")
    def admission_products(
        request: Request,
        entity_id: UUID,
        channel: AdmissionChannel,
        search: str = Query(default="", max_length=100),
    ) -> dict[str, Any]:
        return envelope(
            request, AdmissionPolicies(catalog.uow).products(auth(request), entity_id, search)
        )

    @app.post("/api/v1/quality-sale-stops/{id}")
    def quality_sale_stop(
        request: Request, id: UUID, body: sale_stops.SaleStopSave
    ) -> dict[str, Any]:
        with guarded(request, quality_write=True) as ctx:
            return envelope(request, sale_stops.save(catalog.uow, ctx, id, body, key(request)))

    @app.get("/api/v1/catalog/dictionary-navigation")
    def dictionary_navigation(request: Request) -> dict[str, Any]:
        return envelope(request, CatalogDictionaries(catalog.uow).navigation(auth(request)))

    @app.get("/api/v1/catalog/dictionary-options/{dictionary_key}")
    def dictionary_options(request: Request, dictionary_key: DictionaryKey) -> dict[str, Any]:
        data = CatalogDictionaries(catalog.uow).detail(auth(request), dictionary_key)
        return envelope(request, {**data, "items": [i for i in data["items"] if i["active"]]})

    @app.get("/api/v1/catalog/dictionary-search")
    def dictionary_search(
        request: Request, search: str = Query(default="", max_length=100)
    ) -> dict[str, Any]:
        return envelope(request, CatalogDictionaries(catalog.uow).search(auth(request), search))

    @app.get("/api/v1/management/{kind}")
    def management_list(
        request: Request,
        kind: ManagementKind,
        entity_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, Management(catalog.uow).listing(auth(request), kind, entity_id, search, offset)
        )

    @app.get("/api/v1/management/{kind}/{id}")
    def management_detail(
        request: Request, kind: ManagementKind, id: UUID, entity_id: UUID
    ) -> dict[str, Any]:
        return envelope(request, Management(catalog.uow).detail(auth(request), kind, entity_id, id))

    @app.post("/api/v1/management/{kind}/{id}")
    def management_save(
        request: Request, kind: ManagementKind, id: UUID, body: ManagementSave
    ) -> dict[str, Any]:
        with guarded(request, quality_write=kind == "maintenance") as ctx:
            return envelope(
                request, Management(catalog.uow).save(ctx, kind, id, body, key(request))
            )

    @app.get("/api/v1/approval-policies/purchase")
    def purchase_approval_policy(request: Request, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, ApprovalWorkflows(catalog.uow).get(auth(request), entity_id))

    @app.post("/api/v1/approval-policies/purchase")
    def publish_purchase_approval_policy(request: Request, body: PolicyPublish) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ApprovalWorkflows(catalog.uow).publish(ctx, body, key(request))
            )

    @app.post("/api/v1/purchase-orders/{id}/approval/{action}")
    def purchase_approval_transition(
        request: Request, id: UUID, action: Literal["reject", "resubmit"], body: Transition
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                ApprovalWorkflows(catalog.uow).transition(ctx, id, body, key(request), action),
            )

    @app.get("/api/v1/quality-records")
    def quality_records(
        request: Request,
        filters: Annotated[QualityRecordQuery, Query()],
    ) -> dict[str, Any]:
        return envelope(
            request,
            QualityRecords(catalog.uow).listing(auth(request), filters),
        )

    @app.get(
        "/api/v1/quality-records/export",
        response_class=Response,
        responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
    )
    def export_quality_records(
        request: Request, filters: Annotated[QualityRecordQuery, Query()]
    ) -> Response:
        content, filename, count = QualityRecords(catalog.uow).export(auth(request), filters)
        return Response(
            content=content,
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                "Cache-Control": "no-store",
                "X-Export-Rows": str(count),
            },
        )

    @app.get("/api/v1/messages")
    def messages(
        request: Request,
        entity_id: UUID,
        source: ReminderSource = "work",
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, Management(catalog.uow).messages(auth(request), entity_id, source, offset)
        )

    @app.get("/api/v1/messages/cases")
    def reminder_cases(
        request: Request,
        entity_id: UUID,
        state: Literal["PROCESSING", "CLOSED"] = "PROCESSING",
        mine: bool = False,
        source: ReminderSource | None = None,
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request,
            Reminders(catalog.uow).cases(
                auth(request), entity_id, state, mine, offset, source or ""
            ),
        )

    @app.get("/api/v1/messages/cases/{id}")
    def reminder_case(request: Request, id: UUID, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, Reminders(catalog.uow).detail(auth(request), entity_id, id))

    @app.get("/api/v1/messages/responsible-staff")
    def reminder_staff(
        request: Request, entity_id: UUID, search: str = Query(default="", max_length=100)
    ) -> dict[str, Any]:
        return envelope(request, Reminders(catalog.uow).staff(auth(request), entity_id, search))

    @app.post("/api/v1/messages/actions")
    def reminder_action(request: Request, body: ReminderAction) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, Reminders(catalog.uow).act(ctx, body, key(request)))

    @app.post("/api/v1/messages/read")
    def message_read(request: Request, body: MessageRead) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, Management(catalog.uow).mark_read(ctx, body, key(request)))

    @app.get("/api/v1/catalog/dictionaries")
    def catalog_dictionaries(request: Request) -> dict[str, Any]:
        return envelope(request, CatalogDictionaries(catalog.uow).list(auth(request)))

    @app.get("/api/v1/catalog/dictionaries/{dictionary_key}")
    def catalog_dictionary(request: Request, dictionary_key: DictionaryKey) -> dict[str, Any]:
        return envelope(
            request, CatalogDictionaries(catalog.uow).detail(auth(request), dictionary_key)
        )

    @app.post("/api/v1/catalog/dictionaries/{dictionary_key}")
    def update_catalog_dictionary(
        request: Request, dictionary_key: DictionaryKey, body: DictionaryUpdate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                CatalogDictionaries(catalog.uow).update(ctx, dictionary_key, body, key(request)),
            )

    @app.get("/api/v1/catalog/{kind}/{id}/operations")
    def catalog_operations(request: Request, kind: MasterKind, id: UUID) -> dict[str, Any]:
        return envelope(request, CatalogOperations(catalog.uow).get(auth(request), kind, id))

    @app.post("/api/v1/catalog/{kind}/{id}/operations")
    def update_catalog_operations(
        request: Request, kind: MasterKind, id: UUID, body: OperationsUpdate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, CatalogOperations(catalog.uow).update(ctx, kind, id, body, key(request))
            )

    @app.get("/api/v1/catalog/product-import")
    def product_import_template(request: Request) -> dict[str, Any]:
        return envelope(request, ProductImports(catalog.uow).template(auth(request)))

    @app.post("/api/v1/catalog/product-import/preview")
    def preview_product_import(request: Request, body: ProductImport) -> dict[str, Any]:
        return envelope(request, ProductImports(catalog.uow).preview(auth(request), body))

    @app.post("/api/v1/catalog/product-import")
    def submit_product_import(request: Request, body: ProductImport) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, ProductImports(catalog.uow).submit(ctx, body, key(request)))

    @app.get("/api/v1/catalog/products")
    def products(
        request: Request,
        search: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0, le=100000),
        category: Literal["", "DRUG", "DEVICE"] = "",
        state: Literal["", "DRAFT", "APPROVED", "SUSPENDED"] = "",
    ) -> dict[str, Any]:
        return envelope(request, catalog.products(auth(request), search, offset, category, state))

    @app.post("/api/v1/catalog/products")
    def create_product(request: Request, body: ProductCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, catalog.create_product(ctx, body, key(request)))

    @app.get("/api/v1/catalog/products/{id}")
    def product_detail(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, catalog_maintenance.detail(auth(request), "product", id, offset))

    @app.get("/api/v1/catalog/{area}/{id}/attachments")
    def catalog_attachments(
        request: Request, area: Literal["products", "parties"], id: UUID
    ) -> dict[str, Any]:
        kind: Kind = "product" if area == "products" else "party"
        return envelope(request, CatalogAttachments(catalog.uow).listing(auth(request), kind, id))

    @app.post(
        "/api/v1/catalog/{area}/{id}/attachments",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
        responses={413: {"description": "附件超过 5 MiB"}, 415: {"description": "附件格式不支持"}},
    )
    async def upload_catalog_attachment(
        request: Request,
        area: Literal["products", "parties"],
        id: UUID,
        filename: str = Query(min_length=1, max_length=180),
        reason: str = Query(min_length=1, max_length=500),
        previous_id: UUID | None = None,
    ) -> dict[str, Any]:
        kind: Kind = "product" if area == "products" else "party"
        service = CatalogAttachments(catalog.uow)

        def preflight() -> None:
            service.scope(auth(request, write=True), kind, id, "catalog.write")

        await anyio.to_thread.run_sync(preflight)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
            raise BusinessError("ATTACHMENT_TYPE", "请使用二进制附件上传", 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_BYTES:
                raise BusinessError("ATTACHMENT_SIZE", "单份附件不能超过 5 MiB", 413)
            data.extend(chunk)

        def save() -> dict[str, Any]:
            # Reauthenticate after reading bytes; keep commit guard and transaction in one thread.
            with guarded(request) as ctx:
                return service.upload(
                    ctx, kind, id, filename, bytes(data), reason, key(request), previous_id
                )

        return envelope(request, await anyio.to_thread.run_sync(save))

    @app.get(
        "/api/v1/catalog/{area}/{id}/attachments/{attachment_id}/content",
        response_class=Response,
        responses={
            200: {
                "description": "经过权限与完整性校验的私有文件",
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    def download_catalog_attachment(
        request: Request,
        area: Literal["products", "parties"],
        id: UUID,
        attachment_id: UUID,
    ) -> Response:
        kind: Kind = "product" if area == "products" else "party"
        metadata, data = CatalogAttachments(catalog.uow).download(
            auth(request), kind, id, attachment_id
        )
        return Response(
            data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(metadata["filename"], safe=""),
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-SHA256": metadata["sha256"],
            },
        )

    @app.get("/api/v1/catalog/products/{id}/certificates")
    def product_certificates(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, ProductCertificates(catalog.uow).list(auth(request), id))

    @app.post("/api/v1/catalog/products/{id}/certificates")
    def create_product_certificate(
        request: Request, id: UUID, body: CertificateCreate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, ProductCertificates(catalog.uow).save(ctx, id, body, key(request))
            )

    @app.get("/api/v1/catalog/products/{id}/certificates/{certificate_id}")
    def product_certificate_detail(
        request: Request,
        id: UUID,
        certificate_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request,
            ProductCertificates(catalog.uow).detail(auth(request), id, certificate_id, offset),
        )

    @app.post("/api/v1/catalog/products/{id}/certificates/{certificate_id}/revise")
    def revise_product_certificate(
        request: Request,
        id: UUID,
        certificate_id: UUID,
        body: CertificateRevision,
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                ProductCertificates(catalog.uow).save(ctx, id, body, key(request), certificate_id),
            )

    @app.post("/api/v1/catalog/products/{id}/certificates/{certificate_id}/review")
    def review_product_certificate(
        request: Request,
        id: UUID,
        certificate_id: UUID,
        body: CertificateReview,
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                ProductCertificates(catalog.uow).review(
                    ctx, id, certificate_id, body, key(request)
                ),
            )

    @app.get("/api/v1/catalog/parties/{id}/certificates")
    def party_certificates(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, PartyCertificates(catalog.uow).list(auth(request), id))

    @app.post("/api/v1/catalog/parties/{id}/certificates")
    def create_party_certificate(
        request: Request, id: UUID, body: PartyCertificateCreate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, PartyCertificates(catalog.uow).save(ctx, id, body, key(request))
            )

    @app.get("/api/v1/catalog/parties/{id}/certificates/{certificate_id}")
    def party_certificate_detail(
        request: Request,
        id: UUID,
        certificate_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request,
            PartyCertificates(catalog.uow).detail(auth(request), id, certificate_id, offset),
        )

    @app.post("/api/v1/catalog/parties/{id}/certificates/{certificate_id}/revise")
    def revise_party_certificate(
        request: Request,
        id: UUID,
        certificate_id: UUID,
        body: PartyCertificateRevision,
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                PartyCertificates(catalog.uow).save(ctx, id, body, key(request), certificate_id),
            )

    @app.post("/api/v1/catalog/parties/{id}/certificates/{certificate_id}/review")
    def review_party_certificate(
        request: Request,
        id: UUID,
        certificate_id: UUID,
        body: PartyCertificateReview,
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                PartyCertificates(catalog.uow).review(ctx, id, certificate_id, body, key(request)),
            )

    @app.post("/api/v1/catalog/products/{id}/revise")
    def revise_product(request: Request, id: UUID, body: ProductRevision) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, catalog_maintenance.revise(ctx, "product", id, body, key(request))
            )

    @app.post("/api/v1/catalog/parties/{id}/revise")
    def revise_party(request: Request, id: UUID, body: PartyRevision) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, catalog_maintenance.revise(ctx, "party", id, body, key(request))
            )

    @app.get("/api/v1/catalog/parties/{id}")
    def party_detail(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, catalog_maintenance.detail(auth(request), "party", id, offset))

    @app.post("/api/v1/catalog/products/{id}/review")
    def review_product(request: Request, id: UUID, body: Review) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, catalog.review(ctx, "product", id, body, key(request)))

    @app.get("/api/v1/catalog/parties")
    def parties(
        request: Request,
        entity_id: UUID,
        search: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0, le=100000),
        role: Literal["", "SUPPLIER", "CUSTOMER", "BOTH"] = "",
        state: Literal["", "DRAFT", "APPROVED", "SUSPENDED"] = "",
    ) -> dict[str, Any]:
        return envelope(
            request, catalog.parties(auth(request), entity_id, search, offset, role, state)
        )

    @app.get("/api/v1/catalog/{kind}/{id}/codes")
    def catalog_codes(
        request: Request,
        kind: CodeOwner,
        id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        schemes: Annotated[list[CodeScheme] | None, Query(max_length=12)] = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            CatalogCodes(catalog.uow).list(
                auth(request), kind, id, offset, list(schemes) if schemes else None
            ),
        )

    @app.post("/api/v1/catalog/{kind}/{id}/codes")
    def create_catalog_code(
        request: Request, kind: CodeOwner, id: UUID, body: CatalogCodeCreate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, CatalogCodes(catalog.uow).create(ctx, kind, id, body, key(request))
            )

    @app.post("/api/v1/catalog/{kind}/{id}/codes/{code_id}/state")
    def catalog_code_state(
        request: Request, kind: CodeOwner, id: UUID, code_id: UUID, body: CatalogCodeState
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request,
                CatalogCodes(catalog.uow).change_state(ctx, kind, id, code_id, body, key(request)),
            )

    @app.post("/api/v1/catalog/parties")
    def create_party(request: Request, body: PartyCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, catalog.create_party(ctx, body, key(request)))

    @app.post("/api/v1/catalog/parties/{id}/review")
    def review_party(request: Request, id: UUID, body: Review) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, catalog.review(ctx, "party", id, body, key(request)))

    @app.get("/api/v1/purchasing/options")
    def purchase_options(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        include_skus: bool = True,
        include_parties: bool = True,
    ) -> dict[str, Any]:
        return envelope(
            request,
            procurement.options(auth(request), entity_id, offset, include_skus, include_parties),
        )

    @app.get("/api/v1/purchasing/workflow")
    def purchase_workflow(
        request: Request,
        entity_id: UUID,
        stage: Literal["orders", "receiving", "inspection", "posting"],
        view: Literal["pending", "history"] = "pending",
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=20),
        date_from: date | None = None,
        date_to: date | None = None,
        warehouse_id: UUID | None = None,
        party_id: UUID | None = None,
        sort: Literal["newest", "oldest"] = "newest",
    ) -> dict[str, Any]:
        from medinv.modules.procurement.application.workflow import worklist

        return envelope(
            request,
            worklist(
                procurement.uow,
                auth(request),
                entity_id,
                stage,
                view,
                offset,
                search,
                state,
                date_from,
                date_to,
                warehouse_id,
                party_id,
                sort,
            ),
        )

    @app.get("/api/v1/purchase-orders")
    def orders(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: Literal["", "DRAFT", "OPEN", "RECEIVED", "CLOSED", "CANCELLED"] = "",
        date_from: date | None = None,
        date_to: date | None = None,
        warehouse_id: UUID | None = None,
        party_id: UUID | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            procurement.orders(
                auth(request),
                entity_id,
                offset,
                search,
                state,
                date_from,
                date_to,
                warehouse_id,
                party_id,
            ),
        )

    @app.post("/api/v1/purchase-orders")
    def create_order(request: Request, body: OrderCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.create_order(ctx, body, key(request)))

    @app.get("/api/v1/purchase-orders/{id}")
    def order_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, procurement.detail(auth(request), id))

    @app.get("/api/v1/purchase-orders/{id}/history")
    def purchase_history(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(
            request, purchase_amendments.history(procurement, auth(request), id, offset)
        )

    @app.post("/api/v1/purchase-orders/{id}/revise")
    def revise_purchase(request: Request, id: UUID, body: OrderRevision) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(
                request, purchase_amendments.revise(procurement, ctx, id, body, key(request))
            )

    @app.post("/api/v1/purchase-orders/{id}/approve")
    def approve_order(request: Request, id: UUID, body: Approval) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.transition(ctx, id, body, key(request), True))

    @app.get("/api/v1/purchasing/review-options")
    def purchasing_review_options(request: Request) -> dict[str, Any]:
        return envelope(request, purchase_review_options(procurement.uow, auth(request)))

    @app.post("/api/v1/purchase-orders/{id}/cancel")
    def cancel_order(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.transition(ctx, id, body, key(request), False))

    @app.post("/api/v1/purchase-orders/{id}/close")
    def close_purchase(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.close(ctx, id, body, key(request)))

    @app.post("/api/v1/purchase-orders/{id}/receipts")
    def receive_order(request: Request, id: UUID, body: ReceiptCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.receive(ctx, id, body, key(request)))

    @app.post("/api/v1/receipts/{id}/inspections")
    def inspect_receipt(request: Request, id: UUID, body: InspectionCreate) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.inspect(ctx, id, body, key(request)))

    @app.post("/api/v1/receipts/{id}/rejected-handovers")
    def hand_back_rejected(request: Request, id: UUID, body: RejectedHandover) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, hand_back(procurement, ctx, id, body, key(request)))

    @app.post("/api/v1/receipt-postings/{id}/post")
    def post_receipt(request: Request, id: UUID, body: ReceiptPost) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, procurement.post(ctx, id, body, key(request)))

    @app.get("/api/v1/inventory/ledger")
    def stock_ledger(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: str = Query(default="", max_length=30),
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            procurement.ledger(
                auth(request),
                entity_id,
                offset,
                search=search,
                state=state,
                date_from=date_from,
                date_to=date_to,
            ),
        )

    @app.get("/api/v1/selling/options")
    def sales_options(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        include_skus: bool = True,
        include_parties: bool = True,
    ) -> dict[str, Any]:
        return envelope(
            request, sales.options(auth(request), entity_id, offset, include_skus, include_parties)
        )

    @app.get("/api/v1/selling/stock")
    def sales_stock(
        request: Request,
        entity_id: UUID,
        warehouse_id: UUID,
        sku_id: Annotated[list[UUID], Query(min_length=1, max_length=50)],
        minimum_shelf_days: int = Query(default=0, ge=0, le=3650),
    ) -> dict[str, Any]:
        return envelope(
            request,
            sale_stock.lookup(
                auth(request), "wholesale", entity_id, warehouse_id, sku_id, minimum_shelf_days
            ),
        )

    @app.get("/api/v1/customer-credit/{id}")
    def credit(request: Request, id: UUID, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, sales.credit(auth(request), entity_id, id))

    @app.post("/api/v1/customer-credit/{id}")
    def set_credit(request: Request, id: UUID, entity_id: UUID, body: CreditSet) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, sales.set_credit(ctx, entity_id, id, body, key(request)))

    @app.get("/api/v1/sales-orders")
    def sales_orders(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: Literal["", "DRAFT", "APPROVED", "FULFILLED", "CLOSED", "CANCELLED"] = "",
        date_from: date | None = None,
        date_to: date | None = None,
        warehouse_id: UUID | None = None,
        party_id: UUID | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            sales.orders(
                auth(request),
                entity_id,
                offset,
                search,
                state,
                date_from,
                date_to,
                warehouse_id,
                party_id,
            ),
        )

    @app.post("/api/v1/sales-orders")
    def create_sale(request: Request, body: SalesCreate) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, sales.create(ctx, body, key(request)))

    @app.get("/api/v1/sales-orders/{id}")
    def sales_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, sales.detail(auth(request), id))

    @app.get("/api/v1/sales-orders/{id}/replenishment")
    def replenishment_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, replenishment.detail(auth(request), id))

    @app.post("/api/v1/sales-orders/{id}/replenishment")
    def replenishment_purchase(
        request: Request, id: UUID, body: ReplenishmentCreate
    ) -> dict[str, Any]:
        with guarded(request) as ctx:
            return envelope(request, replenishment.create(ctx, id, body, key(request)))

    @app.post("/api/v1/sales-orders/{id}/approve")
    def approve_sale(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, sales.transition(ctx, id, body, key(request), True))

    @app.get("/api/v1/sales-orders/{id}/history")
    def sales_history(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, sales_amendments.history(sales, auth(request), id, offset))

    @app.post("/api/v1/sales-orders/{id}/revise")
    def revise_sale(request: Request, id: UUID, body: SalesRevision) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, sales_amendments.revise(sales, ctx, id, body, key(request)))

    @app.post("/api/v1/sales-orders/{id}/close")
    def close_sale(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, sales.transition(ctx, id, body, key(request), False))

    @app.post("/api/v1/sales-orders/{id}/shipments")
    def allocate_sale(request: Request, id: UUID, body: ShipmentCreate) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, fulfillment.allocate(sales, ctx, id, body, key(request)))

    @app.post("/api/v1/shipments/{id}/pick")
    def pick_shipment(request: Request, id: UUID, body: Pick) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(
                request, fulfillment.pick_or_verify(sales, ctx, id, body, key(request), False)
            )

    @app.post("/api/v1/shipments/{id}/verify")
    def verify_shipment(request: Request, id: UUID, body: Verify) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(
                request, fulfillment.pick_or_verify(sales, ctx, id, body, key(request), True)
            )

    @app.post("/api/v1/shipments/{id}/cancel")
    def cancel_shipment(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, fulfillment.cancel(sales, ctx, id, body, key(request)))

    @app.post("/api/v1/shipments/{id}/post")
    def post_shipment(request: Request, id: UUID, body: ShipmentPost) -> dict[str, Any]:
        with guarded(request, sales_write=True) as ctx:
            return envelope(request, sales_posting.post(sales, ctx, id, body, key(request)))

    @app.get("/api/v1/receivables")
    def receivables(
        request: Request, entity_id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, sales.receivables(auth(request), entity_id, offset))

    @app.get("/api/v1/settlements/accounts")
    def cash_accounts(request: Request, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, settlement.accounts(auth(request), entity_id))

    @app.post("/api/v1/settlements/accounts")
    def create_cash_account(request: Request, body: AccountCreate) -> dict[str, Any]:
        with guarded(request, settlement_write=True) as ctx:
            return envelope(request, settlement.create_account(ctx, body, key(request)))

    @app.get("/api/v1/settlements/items")
    def settlement_items(
        request: Request,
        entity_id: UUID,
        kind: Literal["AR", "AP"],
        party_id: UUID | None = None,
        open_only: bool = False,
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(
            request, settlement.items(auth(request), entity_id, kind, offset, party_id, open_only)
        )

    @app.get("/api/v1/settlements/cash-documents")
    def cash_documents(
        request: Request, entity_id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, settlement.cash_list(auth(request), entity_id, offset))

    @app.post("/api/v1/settlements/cash-documents")
    def record_cash(request: Request, body: CashCreate) -> dict[str, Any]:
        with guarded(request, settlement_write=True) as ctx:
            return envelope(request, settlement.create_cash(ctx, body, key(request)))

    @app.get("/api/v1/settlements/cash-documents/{id}")
    def cash_detail(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, settlement.detail(auth(request), id, offset))

    @app.post("/api/v1/settlements/cash-documents/{id}/void")
    def void_cash(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, settlement_write=True) as ctx:
            return envelope(request, settlement.void(ctx, id, body, key(request)))

    @app.post("/api/v1/settlements/allocations")
    def allocate_cash(request: Request, body: AllocateRequest) -> dict[str, Any]:
        with guarded(request, settlement_write=True) as ctx:
            return envelope(
                request, settlement_allocation.allocate(settlement, ctx, body, key(request))
            )

    @app.post("/api/v1/settlements/allocations/{id}/reverse")
    def reverse_allocation(request: Request, id: UUID, body: ReverseRequest) -> dict[str, Any]:
        with guarded(request, settlement_write=True) as ctx:
            return envelope(
                request, settlement_allocation.reverse(settlement, ctx, id, body, key(request))
            )

    @app.get("/api/v1/returns/sources")
    def return_sources(
        request: Request,
        entity_id: UUID,
        kind: Literal["SALES", "PURCHASE", "RETAIL"],
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(request, returns.sources(auth(request), entity_id, kind, offset))

    @app.get("/api/v1/returns/sources/{id}")
    def return_source_detail(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, returns.source_detail(auth(request), id, offset))

    @app.get("/api/v1/returns")
    def return_list(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        custody_only: bool = False,
    ) -> dict[str, Any]:
        return envelope(request, returns.listing(auth(request), entity_id, offset, custody_only))

    @app.post("/api/v1/returns")
    def create_return(request: Request, body: ReturnCreate) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, returns.create(ctx, body, key(request)))

    @app.get("/api/v1/returns/{id}")
    def return_detail(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, returns.detail(auth(request), id, offset))

    @app.post("/api/v1/returns/{id}/receive")
    def receive_return(request: Request, id: UUID, body: ReturnReceive) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, custody.receive(returns, ctx, id, body, key(request)))

    @app.post("/api/v1/returns/{id}/hand-back")
    def hand_back_return(request: Request, id: UUID, body: CustodyHandover) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, custody.hand_back(returns, ctx, id, body, key(request)))

    @app.post("/api/v1/returns/{id}/review")
    def review_return(request: Request, id: UUID, body: ReturnReview) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, returns.review(ctx, id, body, key(request)))

    @app.post("/api/v1/returns/{id}/cancel")
    def cancel_return(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, returns.cancel(ctx, id, body, key(request)))

    @app.post("/api/v1/returns/{id}/post")
    def post_return(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, return_posting.post(returns, ctx, id, body, key(request)))

    @app.get("/api/v1/returns/{id}/refund-options")
    def refund_options(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, refunds.options(returns, auth(request), id, offset))

    @app.get("/api/v1/returns/{id}/refunds")
    def refund_list(
        request: Request, id: UUID, offset: int = Query(default=0, ge=0, le=100000)
    ) -> dict[str, Any]:
        return envelope(request, refunds.listing(returns, auth(request), id, offset))

    @app.post("/api/v1/return-refunds")
    def create_refund(request: Request, body: RefundCreate) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, refunds.create(returns, ctx, body, key(request)))

    @app.post("/api/v1/return-refunds/{id}/void")
    def void_refund(request: Request, id: UUID, body: ReverseRequest) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, refunds.void(returns, ctx, id, body, key(request)))

    @app.get("/api/v1/retail/options")
    def retail_options(request: Request, entity_id: UUID) -> dict[str, Any]:
        return envelope(request, retail.options(auth(request), entity_id))

    @app.get("/api/v1/retail/stock")
    def retail_stock(
        request: Request,
        entity_id: UUID,
        warehouse_id: UUID,
        sku_id: Annotated[list[UUID], Query(min_length=1, max_length=50)],
    ) -> dict[str, Any]:
        return envelope(
            request, sale_stock.lookup(auth(request), "retail", entity_id, warehouse_id, sku_id)
        )

    @app.get("/api/v1/retail/catalog")
    def retail_catalog(
        request: Request,
        entity_id: UUID,
        search: str = Query(default="", max_length=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        return envelope(request, retail.catalog(auth(request), entity_id, search, offset))

    @app.get("/api/v1/retail/sales")
    def retail_sales(
        request: Request,
        entity_id: UUID,
        offset: int = Query(default=0, ge=0, le=100000),
        search: str = Query(default="", max_length=100),
        state: Literal[
            "", "RESERVED", "PAID_PENDING_POST", "COMPLETED", "CANCELLED", "REFUNDED"
        ] = "",
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        return envelope(
            request,
            retail.listing(auth(request), entity_id, offset, search, state, date_from, date_to),
        )

    @app.post("/api/v1/retail/sales")
    def create_retail_sale(request: Request, body: RetailCreate) -> dict[str, Any]:
        with guarded(request, retail_write=True) as ctx:
            return envelope(request, retail.create(ctx, body, key(request)))

    @app.get("/api/v1/retail/sales/{id}")
    def retail_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, retail.detail(auth(request), id))

    @app.post("/api/v1/retail/sales/{id}/cash")
    def retail_cash(request: Request, id: UUID, body: RetailPayment) -> dict[str, Any]:
        with guarded(request, retail_write=True) as ctx:
            return envelope(request, retail_payments.receive(retail, ctx, id, body, key(request)))

    @app.post("/api/v1/retail/sales/{id}/complete")
    def complete_retail(request: Request, id: UUID, body: RetailComplete) -> dict[str, Any]:
        with guarded(request, retail_write=True) as ctx:
            return envelope(request, retail_posting.complete(retail, ctx, id, body, key(request)))

    @app.post("/api/v1/retail/sales/{id}/cancel")
    def cancel_retail(request: Request, id: UUID, body: Transition) -> dict[str, Any]:
        with guarded(request, retail_write=True) as ctx:
            return envelope(request, retail.cancel(ctx, id, body, key(request)))

    @app.post("/api/v1/retail/sales/{id}/refund-pending")
    def refund_pending_retail(
        request: Request,
        id: UUID,
        body: RetailCashConfirmation,
    ) -> dict[str, Any]:
        with guarded(request, retail_write=True) as ctx:
            return envelope(
                request, retail_payments.refund_pending(retail, ctx, id, body, key(request))
            )

    @app.get("/api/v1/returns/{id}/retail-refund")
    def retail_return_refund_detail(request: Request, id: UUID) -> dict[str, Any]:
        return envelope(request, retail_refunds.detail(returns, auth(request), id))

    @app.post("/api/v1/returns/{id}/retail-refund")
    def retail_return_refund(
        request: Request,
        id: UUID,
        body: RetailCashConfirmation,
    ) -> dict[str, Any]:
        with guarded(request, returns_write=True) as ctx:
            return envelope(request, retail_refunds.create(returns, ctx, id, body, key(request)))

    install_contract(app)
    return app
