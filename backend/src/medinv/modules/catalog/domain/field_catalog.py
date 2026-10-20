"""Runtime field semantics, separate from tenant presentation preferences.

These compatibility templates describe existing DRUG/DEVICE records. They do not
classify legacy drugs as finished medicines or enable the design-only categories.
"""

from typing import Any

CATALOG_VERSION = "1"
PRODUCT_DIRECT_FIELDS = frozenset({"search_alias"})
QUALITY_CARD_FIELDS = frozenset(
    {
        "device_class",
        "drug_sale_class",
        "approval_number",
        "valid_from",
        "valid_until",
        "storage_conditions",
        "manufacturer_address",
        "authorization_holder",
        "authorization_holder_address",
        "registrant_name",
        "registrant_address",
        "drug_business_scope",
        "device_business_scope",
        "quality_note",
    }
)
LOCKED_LABEL_FIELDS = frozenset(
    {
        "device_class",
        "drug_sale_class",
        "approval_number",
        "valid_from",
        "valid_until",
        "registrant_name",
        "registrant_address",
    }
)
MEANINGS = {
    "name": "商品档案显示名称；药品通用名另行填写。",
    "manufacturer_name": "生产企业名称，与持有人、注册人或供应商身份分别记录。",
    "specification": "用于识别本商品的规格展示文本；器械型号可单独维护。",
    "search_alias": "企业内部检索词或助记码，日常调整无需质量重审。",
    "trade_name": "现有商品名与品牌混合字段，保留旧值；后续拆分须人工核对，改显示名不改变其含义。",
    "approval_number": "旧商品档案使用的批准、注册或备案编号；独立证照档案另行维护。",
    "valid_from": "原有商品资质校验的起始日期，不等同于证照签发日期。",
    "valid_until": "原有商品资质校验的截止日期，尚未由独立证照档案替代。",
    "generic_name": "按药品资料登记的通用名称，不与商品名或品牌混用。",
    "authorization_holder": "药品上市许可持有人，不默认等同于生产企业。",
    "registrant_name": "依器械类别登记备案人或注册人，不默认等同于生产企业。",
    "drug_business_scope": "药品经营范围类别字典值；交易准入联动仍按实施阶段推进。",
    "device_business_scope": "器械经营范围类别字典值；交易准入联动仍按实施阶段推进。",
}


def metadata(key: str) -> dict[str, Any]:
    direct = key in PRODUCT_DIRECT_FIELDS
    return {
        "field_code": f"catalog.product.{key}",
        "card": "operations" if direct else "quality" if key in QUALITY_CARD_FIELDS else "basic",
        "owner_department": "运营管理" if direct else "质量管理",
        "review_policy": "DIRECT_AUDIT" if direct else "QUALITY_REVIEW",
        "alias_allowed": key not in LOCKED_LABEL_FIELDS,
        "meaning": MEANINGS.get(key, "按商品资料维护，修改后纳入质量审核并保留历史记录。"),
    }


def template(category: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    variants = (
        {
            "UNKNOWN": {"code": "DEVICE_UNCLASSIFIED", "name": "器械分类待核定"},
            "I": {"code": "DEVICE_I", "name": "第一类医疗器械"},
            "II": {"code": "DEVICE_II", "name": "第二类医疗器械"},
            "III": {"code": "DEVICE_III", "name": "第三类医疗器械"},
        }
        if category == "DEVICE"
        else {}
    )
    return {
        "code": f"{category}_BASE",
        "version": CATALOG_VERSION,
        "name": "器械基础模板" if category == "DEVICE" else "药品基础模板",
        "scope": "现有资料兼容模板；尚未启用饮片、食品等独立分类模板。",
        "selector": "device_class" if category == "DEVICE" else "drug_sale_class",
        "variants": variants,
        "resolved_variant": variants.get((values or {}).get("device_class", "UNKNOWN")),
    }
