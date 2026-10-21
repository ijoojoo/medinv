import { partyCertificateLabels } from "./partyCertificates";
export const certificateTypes: Record<string, string> = Object.fromEntries(
  [
    "BUSINESS_LICENSE",
    "DRUG_BUSINESS_LICENSE",
    "DRUG_PRODUCTION_LICENSE",
    "DEVICE_BUSINESS_LICENSE",
    "DEVICE_BUSINESS_FILING",
    "DEVICE_PRODUCTION_LICENSE",
    "DEVICE_PRODUCTION_FILING",
    "MEDICAL_INSTITUTION_LICENSE",
    "AUTHORIZATION",
    "OTHER",
  ].map((key) => [key, partyCertificateLabels[key]]),
);
export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
export const namesFor = (keys: string[], names: Record<string, string>) =>
  keys.map((key) => names[key] || key).join("、");
export const certificateGroupsText = (groups: string[][]) =>
  groups
    .map((group) =>
      group.map((key) => certificateTypes[key] || key).join(" / "),
    )
    .join(" ＋ ");
export const subjectNames: Record<string, string> = {
  DISTRIBUTOR: "经营企业",
  REGISTRANT: "注册人 / 备案人 / 持有人",
  MEDICAL_INSTITUTION: "医疗机构",
  OTHER: "其他单位",
};
export const channelNames: Record<string, string> = {
  PURCHASE: "向本企业供货",
  WHOLESALE: "从本企业购货",
};
export interface BusinessScope {
  id: string;
  category: "DRUG" | "DEVICE";
  name: string;
  device_class: "I" | "II" | "III" | null;
  directory_version: string;
  codes: string[];
  excluded_codes: string[];
  include_children: boolean;
  subject_type: string;
  channels: string[];
  certificate_ids: string[];
  exceptions: { rule_id: string; evidence_ref: string; reason: string }[];
  enabled: boolean;
  note: string;
}
export interface QualificationRule {
  id: string;
  name: string;
  enabled: boolean;
  category: "DRUG" | "DEVICE";
  device_classes: string[];
  subjects: string[];
  channels: string[];
  certificate_groups: string[][];
  source_title: string;
  source_url: string;
  article: string;
  effective_from: string;
  effective_until: string | null;
  checked_on: string;
  note: string;
}
export interface QualificationSettings {
  enforcement: "BLOCK" | "WARN" | "OFF";
  impact: "CATEGORY" | "PARTY";
  require_own_scope: boolean;
  legacy_party_validity: boolean;
  product_certificate_required: boolean;
  product_certificate_dates: boolean;
  product_certificate_type_check: boolean;
  product_regulatory_subject_required: boolean;
  certificate_evidence_required: boolean;
  authorization_contact_required: boolean;
  party_certificate_validity_confirmed: boolean;
  product_fixed_period_types: string[];
  rules: QualificationRule[];
}
export const splitCodes = (text: string) => [
  ...new Set(
    text
      .split(/[，,；;\s]+/)
      .map((v) => v.trim())
      .filter(Boolean),
  ),
];
