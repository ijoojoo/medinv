export const certificateNames: Record<string, string> = {
  certificate_type: "证照类型",
  number: "证照编号",
  holder_name: "持有人 / 注册人 / 备案人",
  issuer: "发证 / 备案机关",
  covered_models: "覆盖规格型号",
  issued_on: "签发 / 备案日期",
  validity_mode: "有效期方式",
  valid_from: "有效期起始日期",
  valid_until: "有效期截止日期",
  evidence_ref: "原件凭据编号",
  note: "证照备注",
  attachment_id: "关联附件版本",
  state: "证照核验状态",
};
export const certificateLabels: Record<string, string> = {
  DRUG_APPROVAL: "药品批准证明",
  DEVICE_REGISTRATION: "医疗器械注册证",
  DEVICE_FILING: "第一类医疗器械产品备案",
  FIXED: "固定有效期",
  NOT_STATED: "原件未载明有效期",
  UNCONFIRMED: "待核实",
  DRAFT: "待核验",
  VERIFIED: "已核验",
  INACTIVE: "已停用",
  UPCOMING: "尚未生效",
  EXPIRED: "已到期",
  IN_PERIOD: "在记录有效期内",
  MATCH: "类别匹配",
  MISMATCH: "与当前商品类别不符",
};
export interface CertificateValues {
  certificate_type: string;
  number: string;
  holder_name: string;
  issuer: string;
  covered_models: string;
  issued_on: string | null;
  validity_mode: string;
  valid_from: string | null;
  valid_until: string | null;
  evidence_ref: string;
  note: string;
  attachment_id: string | null;
}
export function certificateValue(value: unknown, key: string): string {
  if (key === "attachment_id")
    return value ? "已关联固定附件版本" : "未关联附件";
  if (["certificate_type", "validity_mode", "state"].includes(key))
    return certificateLabels[String(value)] || String(value ?? "未填写");
  return value === null || value === undefined || value === ""
    ? "未填写"
    : String(value);
}
export interface CertificateRecord extends CertificateValues {
  id: string;
  version: string;
  state: string;
  validity: string;
  compatibility: string;
  created_by: string;
  revision_editors: string[];
}
export function emptyCertificate(type = "DEVICE_FILING"): CertificateValues {
  return {
    certificate_type: type,
    number: "",
    holder_name: "",
    issuer: "",
    covered_models: "",
    issued_on: null,
    validity_mode: "UNCONFIRMED",
    valid_from: null,
    valid_until: null,
    evidence_ref: "",
    note: "",
    attachment_id: null,
  };
}
export function certificateValues(row: CertificateValues): CertificateValues {
  const result = Object.fromEntries(
    Object.keys(emptyCertificate()).map((k) => [
      k,
      row[k as keyof CertificateValues],
    ]),
  ) as unknown as CertificateValues;
  for (const key of ["issued_on", "valid_from", "valid_until"] as const)
    result[key] ||= null;
  return result;
}
