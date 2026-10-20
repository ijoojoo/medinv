"""Explicit, versioned purchase-line display contract. Never expose arbitrary MDM values."""

from typing import Any, Literal

from medinv.modules.catalog.application.document_master import definitions

Stage = Literal["orders", "receiving", "inspection", "posting", "sales_orders"]
Mode = Literal["editor", "view"]
STAGES = ("orders", "receiving", "inspection", "posting")


def field(
    key: str,
    label: str,
    source: str,
    description: str,
    *,
    stages: tuple[str, ...] = STAGES,
    kind: str = "text",
    width: int = 150,
    edit: tuple[str, ...] = (),
    required: bool = False,
    defaults: tuple[str, ...] = (),
    categories: tuple[str, ...] = (),
) -> dict[str, Any]:
    return dict(
        key=key,
        label=label,
        source=source,
        description=description,
        stages=stages,
        type=kind,
        width=width,
        editor_stages=edit,
        required=required,
        defaults=defaults,
        categories=categories,
        permission="purchase.read",
        snapshot_source="purchase_order_line.document_snapshot"
        if source == "master"
        else "business_line",
        resolver=key,
    )


FIELDS = [
    field(
        "product",
        "商品",
        "common",
        "选购商品；历史显示制单时的名称",
        width=270,
        edit=("orders", "receiving"),
        required=True,
        defaults=STAGES,
    ),
    field("code", "商品编码", "master", "商品主数据编码；历史单据保留制单时记录的编码"),
    field(
        "generic_name",
        "通用名称",
        "master",
        "药品、饮片的通用名称，与商品展示名称分开",
        categories=("DRUG", "HERB"),
    ),
    field("specification", "规格型号", "master", "商品规格；器械型号可另加一列"),
    field("device_model", "器械型号", "master", "医疗器械专用型号", categories=("DEVICE",)),
    field("dosage_form", "剂型", "master", "药品剂型", categories=("DRUG",)),
    field(
        "manufacturer_name",
        "生产企业",
        "master",
        "仅生产 / 受托生产组织，不用注册人代替",
        width=220,
    ),
    field(
        "regulatory_subject",
        "持有人／注册人／备案人",
        "master",
        "共用商品资料的监管主体：按品类、子类型和器械类别取上市许可持有人、注册人或备案人；"
        "包括药品、饮片、器械、特殊食品及化妆品，生产企业独立显示",
        width=240,
        categories=("DRUG", "HERB", "DEVICE", "HEALTH", "FSMP", "INFANT", "COSMETIC"),
    ),
    field(
        "approval_number",
        "批准 / 注册 / 备案编号",
        "master",
        "明确业务使用的证件编号；不与生产企业混用",
        width=220,
    ),
    field("storage_conditions", "贮存条件", "master", "制单时的商品贮存要求", width=200),
    field(
        "base_unit",
        "基本单位",
        "common",
        "库存数量计量单位",
        width=100,
        defaults=("receiving", "inspection", "posting"),
    ),
    field(
        "pack",
        "交易单位",
        "common",
        "本单选用的交易包装单位",
        width=160,
        stages=("orders",),
        edit=("orders",),
        defaults=("orders",),
    ),
    field(
        "conversion", "包装换算", "common", "本单包装版本的换算关系", width=190, stages=("orders",)
    ),
    field(
        "qty",
        "数量",
        "stage",
        "订单按交易单位、到货按基本单位录入",
        kind="number",
        width=110,
        stages=("orders", "receiving"),
        edit=("orders", "receiving"),
        required=True,
        defaults=("orders", "receiving"),
    ),
    field(
        "base_qty",
        "基本单位数量",
        "derived",
        "订单包装折算后的库存数量",
        kind="number",
        stages=("orders",),
        defaults=("orders",),
    ),
    field(
        "price",
        "含税单价",
        "stage",
        "订单交易单位含税单价，原值精度不受显示格式影响",
        kind="number",
        width=130,
        stages=("orders",),
        edit=("orders",),
        required=True,
        defaults=("orders",),
    ),
    field(
        "tax",
        "税率",
        "stage",
        "0.13 与 13% 仅显示方式不同",
        kind="tax",
        width=110,
        stages=("orders",),
        edit=("orders",),
        defaults=("orders",),
    ),
    field(
        "amount",
        "含税金额",
        "derived",
        "订单行含税金额，按原交易金额规则计算",
        kind="number",
        width=130,
        stages=("orders",),
        defaults=("orders",),
    ),
    field(
        "received_qty",
        "已收数量",
        "stage",
        "累计到货基本单位数量",
        kind="number",
        stages=("orders",),
        defaults=("orders",),
    ),
    field(
        "remaining_qty",
        "待收数量",
        "stage",
        "订单基本数量减已收及关闭数量",
        kind="number",
        stages=("orders",),
        defaults=("orders",),
    ),
    field(
        "closed_qty",
        "已关闭数量",
        "stage",
        "已确认停止采购的余量",
        kind="number",
        stages=("orders",),
        defaults=("orders",),
    ),
    field(
        "location",
        "货位",
        "stage",
        "本次到货的实际保管货位",
        width=160,
        stages=("receiving",),
        edit=("receiving",),
        required=True,
        defaults=("receiving",),
    ),
    field(
        "lot_no",
        "生产批号",
        "stage",
        "来源为到货批次，不是商品主档",
        stages=("receiving", "inspection", "posting"),
        edit=("receiving",),
        required=True,
        defaults=("receiving", "inspection", "posting"),
    ),
    field(
        "mfg_date",
        "生产日期",
        "stage",
        "到货批次生产日期，支持八位日期输入",
        kind="date",
        stages=("receiving", "inspection", "posting"),
        edit=("receiving",),
        required=True,
        defaults=("receiving",),
    ),
    field(
        "expiry_date",
        "有效期至",
        "stage",
        "到货批次原始有效期",
        kind="date",
        stages=("receiving", "inspection", "posting"),
        edit=("receiving",),
        required=True,
        defaults=("receiving", "inspection", "posting"),
    ),
    field(
        "pending_qty",
        "待验数量",
        "stage",
        "到货尚未形成验收结论的数量",
        kind="number",
        stages=("receiving", "inspection"),
        defaults=("receiving", "inspection"),
    ),
    field(
        "qualified_qty",
        "合格数量",
        "stage",
        "验收记录为本次结论；到货记录为累计数量",
        kind="number",
        stages=("receiving", "inspection", "posting"),
        edit=("inspection",),
        required=True,
        defaults=("receiving", "inspection", "posting"),
    ),
    field(
        "rejected_qty",
        "拒收数量",
        "stage",
        "验收记录为本次结论；到货记录为累计数量",
        kind="number",
        stages=("receiving", "inspection"),
        edit=("inspection",),
        required=True,
        defaults=("receiving", "inspection"),
    ),
    field(
        "posted_qty",
        "已入库数量",
        "stage",
        "实际完成过账的基本单位数量",
        kind="number",
        stages=("receiving", "posting"),
        defaults=("receiving", "posting"),
    ),
    field(
        "custody_qty",
        "未入库保管",
        "stage",
        "到货减已入库及拒收已退",
        kind="number",
        stages=("receiving",),
        defaults=("receiving",),
    ),
    field(
        "rejected_returned_qty",
        "拒收已退",
        "stage",
        "拒收实物已交还数量",
        kind="number",
        stages=("receiving",),
        defaults=("receiving",),
    ),
]


def registry(
    stage: Stage,
    mode: Mode,
    schema: dict[str, Any] | None = None,
    permissions: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    sales = stage == "sales_orders"
    if sales:
        stage = "orders"
    master = {f["key"]: f for f in definitions(schema)}
    fields = [f.copy() for f in FIELDS]
    existing = {f["key"] for f in fields}
    for key, meta in master.items():
        if key not in existing and (permissions is None or meta["permission"] in permissions):
            fields.append(
                field(
                    key,
                    meta["label"],
                    "master",
                    meta["description"],
                    kind=meta["type"],
                    width=200,
                    categories=meta["categories"],
                )
            )
    if sales:
        fields = [f for f in fields if f["key"] not in {"received_qty", "closed_qty"}]
        fields.extend(
            [
                field(
                    "reserved_qty",
                    "已分配",
                    "stage",
                    "当前已分配基本单位数量",
                    kind="number",
                    defaults=("orders",),
                ),
                field(
                    "fulfilled_qty",
                    "已出库",
                    "stage",
                    "累计出库基本单位数量",
                    kind="number",
                    defaults=("orders",),
                ),
            ]
        )
    result = []
    for f in fields:
        if stage not in f["stages"]:
            continue
        # Pending quantity is a live receiving balance, not an immutable
        # historical inspection result. Do not offer it in inspection history.
        if stage == "inspection" and mode == "view" and f["key"] == "pending_qty":
            continue
        editable = mode == "editor" and stage in f["editor_stages"]
        required = f["key"] == "product" or (editable and f["required"])
        visible = stage in f["defaults"]
        if (
            mode == "editor"
            and stage == "orders"
            and f["key"]
            in {
                "base_qty",
                "received_qty",
                "remaining_qty",
                "closed_qty",
                "reserved_qty",
                "fulfilled_qty",
            }
        ):
            visible = False
        if (
            mode == "editor"
            and stage == "receiving"
            and f["key"]
            in {
                "pending_qty",
                "qualified_qty",
                "rejected_qty",
                "posted_qty",
                "custody_qty",
                "rejected_returned_qty",
            }
        ):
            visible = False
        label = "到货数量" if stage == "receiving" and f["key"] == "qty" else f["label"]
        if sales and f["key"] == "remaining_qty":
            label = "未分配"
            f["description"] = "订单基本单位数量减已分配与已出库数量"
        meta = master.get(f["key"], {})
        result.append(
            {
                **f,
                "group": meta.get("tab", f["source"]),
                "group_label": meta.get("group_label"),
                "permission": meta.get("permission")
                if f["key"] not in existing
                else ("sales.read" if sales else "purchase.read"),
                "snapshot_source": "sales_order_line.document_snapshot"
                if sales and f["source"] == "master"
                else f["snapshot_source"],
                "label": label,
                "editable": editable,
                "required": required,
                "can_hide": not required,
                "stop_required": editable
                and (
                    (stage == "orders" and f["key"] in {"product", "qty"})
                    or (stage == "receiving" and f["key"] == "qty")
                    or (stage == "inspection" and f["key"] == "qualified_qty")
                ),
                "default": dict(
                    key=f["key"],
                    label=label,
                    visible=visible,
                    width_mode="px",
                    width=f["width"],
                    tab_stop=editable
                    and f["key"] not in {"pack", "tax"}
                    and not (stage == "receiving" and f["key"] in {"product", "location"})
                    and not (stage == "inspection" and f["key"] == "rejected_qty"),
                    decimals=-1,
                    grouping=False,
                    wrap=False,
                    tax_display="decimal",
                ),
            }
        )
    return result
