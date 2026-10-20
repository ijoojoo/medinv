import { certificateLabels } from "./productCertificates";

export const partyCertificateNames: Record<string, string> = {
  certificate_type: "证照类型",
  number: "证照编号",
  holder_name: "证照主体 / 授权单位",
  issuer: "发证机关 / 签发单位",
  business_uses: "适用业务",
  scope_text: "原件经营范围",
  authorized_name: "授权联系人",
  authorized_phone: "授权联系电话",
  authorization_duties: "授权事项",
  issued_on: "签发日期",
  validity_mode: "有效期方式",
  valid_from: "有效期起始日期",
  valid_until: "有效期截止日期",
  evidence_ref: "原件凭据编号",
  note: "证照备注",
  state: "证照核验状态",
};
export const partyCertificateLabels: Record<string, string> = {
  ...certificateLabels,
  BUSINESS_LICENSE: "营业执照",
  DRUG_BUSINESS_LICENSE: "药品经营许可证",
  DRUG_PRODUCTION_LICENSE: "药品生产许可证",
  DEVICE_BUSINESS_LICENSE: "医疗器械经营许可证",
  DEVICE_BUSINESS_FILING: "医疗器械经营备案",
  DEVICE_PRODUCTION_LICENSE: "医疗器械生产许可证",
  DEVICE_PRODUCTION_FILING: "医疗器械生产备案",
  MEDICAL_INSTITUTION_LICENSE: "医疗机构执业许可证",
  AUTHORIZATION: "授权书 / 授权联系人",
  OTHER: "其他证照",
  SUPPLY: "作为供应商供货",
  PURCHASE: "作为客户购货",
  LONG_TERM: "原件载明长期",
  MATCH: "适用业务匹配",
  MISMATCH: "用途与当前供销身份不符",
};
export interface PartyCertificateValues {
  certificate_type: string;
  number: string;
  holder_name: string;
  issuer: string;
  business_uses: string[];
  scope_text: string;
  authorized_name: string;
  authorized_phone: string;
  authorization_duties: string;
  issued_on: string | null;
  validity_mode: string;
  valid_from: string | null;
  valid_until: string | null;
  evidence_ref: string;
  note: string;
}
export interface PartyCertificateRecord extends PartyCertificateValues {
  id: string;
  version: string;
  state: string;
  validity: string;
  compatibility: string;
  created_by: string;
  revision_editors: string[];
}
export function partyCertificateValue(value: unknown, key: string): string {
  if (Array.isArray(value))
    return value
      .map((v) => partyCertificateLabels[String(v)] || String(v))
      .join("、");
  if (["certificate_type", "validity_mode", "state"].includes(key))
    return partyCertificateLabels[String(value)] || String(value ?? "未填写");
  return value === null || value === undefined || value === ""
    ? "未填写"
    : String(value);
}
export function emptyPartyCertificate(
  type = "BUSINESS_LICENSE",
): PartyCertificateValues {
  return {
    certificate_type: type,
    number: "",
    holder_name: "",
    issuer: "",
    business_uses: [],
    scope_text: "",
    authorized_name: "",
    authorized_phone: "",
    authorization_duties: "",
    issued_on: null,
    validity_mode: "UNCONFIRMED",
    valid_from: null,
    valid_until: null,
    evidence_ref: "",
    note: "",
  };
}
export function partyCertificateValues(
  row: PartyCertificateValues,
): PartyCertificateValues {
  const result = Object.fromEntries(
    Object.keys(emptyPartyCertificate()).map((k) => [
      k,
      row[k as keyof PartyCertificateValues],
    ]),
  ) as unknown as PartyCertificateValues;
  result.business_uses = [...row.business_uses].sort();
  for (const key of ["issued_on", "valid_from", "valid_until"] as const)
    result[key] ||= null;
  return result;
}
