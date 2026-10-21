import { validationPatterns, type ProductFieldRule } from "./productFieldRules";
export interface DictionaryBinding {
  category: string;
  category_name: string;
  field: string;
  label: string;
  required: boolean;
  required_when: { field: string; equals: string }[];
  source?: "MDM";
  reject_placeholders?: boolean;
  min_length: number;
  max_length: number;
  validator: ProductFieldRule["validator"];
}
export interface CatalogDictionary {
  key: string;
  name: string;
  capacity: number;
  version: string;
  mode: "SUGGEST" | "STRICT";
  group_key?: string;
  dictionary_id?: string;
  kind?: "SYSTEM" | "HYBRID" | "TENANT" | "TREE";
  hierarchical?: boolean;
  extendable?: boolean;
  count?: number;
  usage?: { scope: string; supported: boolean; counts: Record<string, number> };
  items: {
    value: string;
    active: boolean;
    order: number;
    id?: string;
    code?: string;
    description?: string;
    parent_id?: string | null;
    origin?: "SYSTEM" | "TENANT";
    is_default?: boolean;
  }[];
  bindings?: DictionaryBinding[];
  history?: {
    created_at: string;
    created_by: string;
    detail: { reason: string; version: string };
  }[];
}
export function matchesDictionaryValue(
  value: string,
  min = 0,
  max = 500,
  pattern?: string,
) {
  const length = [...value].length;
  return (
    length >= min &&
    length <= max &&
    (!pattern || new RegExp(`^(?:${pattern})$`, "u").test(value))
  );
}
export function checkDictionary(
  dictionary: Pick<CatalogDictionary, "mode" | "items">,
  rule: Pick<
    DictionaryBinding,
    "min_length" | "max_length" | "validator" | "required" | "required_when"
  > & { locked_required?: boolean; reject_placeholders?: boolean },
) {
  const active = dictionary.items.filter((i) => i.active && i.value.trim());
  const incompatible = active
    .filter(
      (i) =>
        (rule.reject_placeholders &&
          ["—", "未知", "不适用"].includes(i.value.trim())) ||
        !matchesDictionaryValue(
          i.value.trim(),
          rule.min_length,
          rule.max_length,
          validationPatterns[rule.validator],
        ),
    )
    .map((i) => i.value);
  const eligible = active.length - incompatible.length;
  const required =
    rule.required || rule.locked_required || rule.required_when.length > 0;
  return {
    active: active.length,
    eligible,
    incompatible,
    blocked: !!required && dictionary.mode === "STRICT" && eligible === 0,
  };
}
export const operationalNames: Record<string, string> = {
  recommendation_level: "推荐级别",
  abc_class: "ABC 分类 / 第一维",
  abc_secondary_class: "双 ABC / 第二维",
  business_note: "经营备注",
  search_alias: "检索别名 / 助记码",
  short_name: "单位简称",
  contact_name: "联系人",
  contact_phone: "联系电话",
  delivery_address: "默认收发货地址",
};
