import type { CatalogDictionary } from "./catalogDictionaries";
import type { ProductFieldRule } from "./productFieldRules";

export interface MasterField {
  key: string;
  field_id: string;
  default_label: string;
  semantic_definition: string;
  help_text?: string;
  value_type: string;
  tab: string;
  binding_key: string;
  profiles: string[];
  read_permission: string;
  write_permission: string;
  lifecycle: string;
  alias_allowed: boolean;
  required: boolean;
  options: { value: string; label: string }[];
  [key: string]: unknown;
}
export interface MasterRule {
  min_length?: number;
  max_length?: number;
  validator?: ProductFieldRule["validator"];
  applicable: boolean;
  label: string;
  required: boolean;
  read_only: boolean;
  required_when: { field: string; equals: string } | null;
}
export interface MasterNode {
  node_id: string;
  node_code: string;
  name: string;
  parent_id: string | null;
  sort_order: number;
  enabled: boolean;
  is_assignable: boolean;
  field_profile_id: string | null;
}
export interface MasterSchema {
  format: number;
  tabs: { id: string; label: string }[];
  fields: MasterField[];
  slots: {
    slot_id: string;
    field_key: string;
    tab: string;
    position: number;
    height: number;
    span: number;
  }[];
  nodes: MasterNode[];
  rules: Record<string, Record<string, MasterRule>>;
  columns: {
    key: string;
    label: string;
    value_type: string;
    projection: string;
  }[];
}
export interface SchemaResponse {
  revision: number;
  payload: MasterSchema;
  subtypes: Record<string, [string, string][]>;
  role_labels: Record<string, string>;
  dictionaries?: Record<string, CatalogDictionary>;
  history: {
    id: string;
    revision: number;
    version: number;
    state: string;
    published_at: string | null;
  }[];
}
export interface MasterIssue {
  field: string;
  tab: string;
  slot_id: string;
  message: string;
}
export type MasterValues = Record<string, unknown>;
export interface MasterRecord {
  id: string;
  has_changes?: boolean;
  version: number;
  data_version: number;
  form_revision: number;
  category_node_id: string;
  category_path: string[];
  profile: string;
  approval_state: string;
  values: MasterValues;
  skus: { id: string; code: string; base_unit: string }[];
  issues: MasterIssue[];
  history: {
    data_version: number;
    form_revision: number;
    created_at: string;
  }[];
  legacy: Record<string, string | null>;
}
export interface MasterRow {
  id: string;
  has_changes?: boolean;
  values: MasterValues;
  category_path: string[];
  approval_state: string;
  states: Record<string, string>;
}
export interface MasterList {
  items: MasterRow[];
  total: number;
  has_more: boolean;
  counts: Record<string, number>;
  columns: MasterSchema["columns"];
  scope: string[];
}
export interface Organization {
  id: string;
  name: string;
  kind: string;
  parent_id: string | null;
  legal_code: string;
}
export interface RoleLink {
  organization_id: string;
  role_code: string;
  evidence_ref: string;
  organization_name?: string;
}
export function roleFor(profile: string, values: MasterValues): string | null {
  if (
    profile === "drug" ||
    (profile === "herb" && values.regulatory_subtype === "APPROVAL_HERB")
  )
    return "DRUG_MAH";
  if (profile === "device")
    return (
      (
        {
          I: "DEVICE_FILER",
          II: "DEVICE_REGISTRANT",
          III: "DEVICE_REGISTRANT",
        } as Record<string, string>
      )[String(values.device_class)] || null
    );
  return (
    (
      {
        HEALTH_REGISTERED: "HEALTH_REGISTRANT",
        HEALTH_FILED: "HEALTH_FILER",
        FSMP_REGISTERED: "FSMP_REGISTRANT",
        INFANT_POWDER: "INFANT_REGISTRANT",
        COSMETIC_GENERAL: "COSMETIC_FILER",
        COSMETIC_SPECIAL: "COSMETIC_REGISTRANT",
      } as Record<string, string>
    )[String(values.regulatory_subtype)] || null
  );
}
export function fieldsFor(
  data: SchemaResponse,
  profile: string,
  values: MasterValues,
) {
  return data.payload.fields.map((f) => {
    const r = data.payload.rules[profile]?.[f.key] || {
      applicable: false,
      label: "",
      required: false,
      read_only: true,
      required_when: null,
    };
    const field = {
      ...f,
      ...r,
      required: r.required,
      label: r.label || f.default_label,
      unconfirmed: false,
    };
    if (f.key === "regulatory_subtype")
      field.options = (data.subtypes[profile] || []).map(([value, label]) => ({
        value,
        label,
      }));
    if (f.key === "regulatory_subject") {
      const role = roleFor(profile, values);
      field.unconfirmed =
        ["device", "herb", "health", "fsmp", "infant", "cosmetic"].includes(
          profile,
        ) &&
        !values[profile === "device" ? "device_class" : "regulatory_subtype"];
      field.applicable = !!role || field.unconfirmed;
      field.label = role
        ? data.role_labels[role]
        : field.unconfirmed
          ? "监管主体（先明确子类型 / 类别）"
          : "上市许可持有人 / 注册人 / 备案人";
    }
    if (f.key === "base_unit" && profile === "service")
      field.label = r.label || "计费单位";
    if (
      r.required_when &&
      values[r.required_when.field] === r.required_when.equals
    )
      field.required = true;
    return field;
  });
}
export function nodePath(schema: MasterSchema, id: string): string[] {
  const path: string[] = [],
    seen = new Set<string>();
  let node = schema.nodes.find((n) => n.node_id === id);
  while (node && !seen.has(node.node_id)) {
    seen.add(node.node_id);
    path.unshift(node.name);
    node = schema.nodes.find((n) => n.node_id === node?.parent_id);
  }
  return path;
}
export function descendants(schema: MasterSchema, id: string) {
  const result = new Set([id]);
  for (let i = 0; i < schema.nodes.length; i++) {
    const before = result.size;
    schema.nodes.forEach((n) => {
      if (n.parent_id && result.has(n.parent_id)) result.add(n.node_id);
    });
    if (result.size === before) break;
  }
  return result;
}
export function summary(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未填写";
  if (Array.isArray(value))
    return value.length
      ? value
          .map((v) =>
            typeof v === "object" && v
              ? String(
                  (v.organization_name
                    ? `${v.role_label || v.role_code}：${v.organization_name}`
                    : null) ||
                    v.snapshot?.number ||
                    v.number ||
                    v.external_code ||
                    v.certificate_id ||
                    "已关联",
                )
              : String(v),
          )
          .join("；")
      : "尚未关联";
  return typeof value === "boolean" ? (value ? "是" : "否") : String(value);
}
export function csvCell(value: string) {
  const safe = /^[=+@\-\t\r]/.test(value) ? "'" + value : value;
  return '"' + safe.replaceAll('"', '""') + '"';
}
export function downloadCsv(name: string, rows: string[][]) {
  const blob = new Blob(
    ["\ufeff" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n")],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
