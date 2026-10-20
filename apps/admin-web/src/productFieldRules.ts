import type { CatalogDictionary } from "./catalogDictionaries";
export interface Condition {
  field: "device_class" | "drug_sale_class";
  equals: string;
}
export interface ProductFieldRule {
  key: string;
  label: string;
  display_name: string;
  field_code: string;
  card: "basic" | "quality" | "operations";
  owner_department: string;
  review_policy: "DIRECT_AUDIT" | "QUALITY_REVIEW";
  alias_allowed: boolean;
  meaning: string;
  kind: "text" | "date" | "select";
  capacity: number;
  locked_required: boolean;
  options: Record<string, string>;
  label_when: (Condition & { label: string })[];
  visible: boolean;
  required: boolean;
  order: number;
  min_length: number;
  max_length: number;
  validator: "NONE" | "NO_WHITESPACE" | "ASCII_CODE" | "DIGITS";
  required_when: Condition[];
  dictionary?: CatalogDictionary | null;
}
export interface ProductFieldPresentation {
  category: "DRUG" | "DEVICE";
  version: string;
  fields: ProductFieldRule[];
  context?: Record<string, string>;
  template: {
    code: string;
    version: string;
    name: string;
    scope: string;
    selector: string;
    variants: Record<string, { code: string; name: string }>;
    resolved_variant: { code: string; name: string } | null;
  };
}
export interface ProductFieldPolicy extends ProductFieldPresentation {
  unit_dictionary?: CatalogDictionary;
  legal_source: string | null;
  history: {
    created_at: string;
    detail: { reason: string; version: string };
  }[];
}
export const fieldCards = {
  basic: "基本信息",
  quality: "质量管控",
  operations: "经营信息",
};
export const fieldReviews = {
  DIRECT_AUDIT: "直接保存 · 留痕",
  QUALITY_REVIEW: "修改后重新审核",
};
export const validationOptions = {
  NONE: "不限格式",
  NO_WHITESPACE: "不含空白字符",
  ASCII_CODE: "字母、数字及 - _ . /",
  DIGITS: "仅数字",
};
export const validationPatterns = {
  NONE: undefined,
  NO_WHITESPACE: "\\S+",
  ASCII_CODE: "[A-Za-z0-9_.\\-\\/]+",
  DIGITS: "[0-9]+",
};
export const productCoreFields = [
  "name",
  "manufacturer_name",
  "specification",
  "approval_number",
  "valid_from",
  "valid_until",
  "drug_sale_class",
] as const;
export function effectiveFields(
  fields: ProductFieldRule[],
  values: Record<string, string>,
) {
  return fields
    .map((field) => {
      const required =
        field.locked_required ||
        field.required ||
        field.required_when.some((c) => values[c.field] === c.equals);
      const label =
        field.label_when.find((c) => values[c.field] === c.equals)?.label ||
        field.display_name ||
        field.label;
      return {
        ...field,
        label,
        required,
        shown:
          field.visible ||
          required ||
          (!!values[field.key] &&
            (field.kind !== "select" || values[field.key] !== "UNKNOWN")),
      };
    })
    .sort((a, b) => a.order - b.order || a.key.localeCompare(b.key));
}
export function writableRules(fields: ProductFieldRule[]) {
  return fields.map(
    ({
      key,
      display_name,
      visible,
      required,
      order,
      min_length,
      max_length,
      validator,
      required_when,
    }) => ({
      key,
      display_name,
      visible,
      required,
      order,
      min_length,
      max_length,
      validator,
      required_when: required_when.map((c) => ({ ...c })),
    }),
  );
}
export function credentialLabel(category?: string, deviceClass?: string) {
  return category === "DRUG"
    ? "批准编号"
    : deviceClass === "I"
      ? "产品备案号"
      : ["II", "III"].includes(deviceClass || "")
        ? "注册证号"
        : "批准 / 注册 / 备案编号";
}
