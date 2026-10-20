export const organizationTypes: Record<string, string> = {
  UNKNOWN: "待核定",
  MANUFACTURER: "生产企业",
  WHOLESALER: "批发企业",
  RETAILER: "零售企业",
  MEDICAL_INSTITUTION: "医疗机构",
  OTHER: "其他单位",
};
export const partyFields = [
  { key: "short_name", label: "单位简称", max: 100, group: "识别与联系" },
  {
    key: "search_alias",
    label: "检索别名 / 助记码",
    max: 100,
    group: "识别与联系",
  },
  { key: "organization_type", label: "机构性质", max: 30, group: "识别与联系" },
  { key: "contact_name", label: "联系人", max: 100, group: "识别与联系" },
  { key: "contact_phone", label: "联系电话", max: 50, group: "识别与联系" },
  {
    key: "legal_representative",
    label: "法定代表人 / 负责人",
    max: 100,
    group: "识别与联系",
  },
  {
    key: "registered_address",
    label: "注册地址",
    max: 300,
    group: "地址与经营范围",
  },
  {
    key: "business_address",
    label: "经营地址",
    max: 300,
    group: "地址与经营范围",
  },
  {
    key: "delivery_address",
    label: "默认收发货地址",
    max: 300,
    group: "地址与经营范围",
  },
  {
    key: "business_scope_text",
    label: "许可 / 备案经营范围原文",
    max: 1000,
    group: "地址与经营范围",
  },
  {
    key: "qualification_archive_ref",
    label: "资质档案编号",
    max: 120,
    group: "核验档案",
  },
  {
    key: "authorization_archive_ref",
    label: "授权委托档案编号",
    max: 120,
    group: "核验档案",
  },
  {
    key: "quality_agreement_ref",
    label: "质量协议编号",
    max: 120,
    group: "核验档案",
  },
] as const;
export type PartyProfile = Record<(typeof partyFields)[number]["key"], string>;
export function partyFieldTab(key: string): "basic" | "operations" | "quality" {
  if (["contact_name", "contact_phone", "delivery_address"].includes(key))
    return "operations";
  if (
    [
      "business_address",
      "business_scope_text",
      "qualification_archive_ref",
      "authorization_archive_ref",
      "quality_agreement_ref",
    ].includes(key)
  )
    return "quality";
  return "basic";
}
export const partyFieldNames = Object.fromEntries(
  partyFields.map((f) => [f.key, f.label]),
);
export function readPartyProfile(
  value: Partial<PartyProfile> = {},
): PartyProfile {
  return Object.fromEntries(
    partyFields.map((f) => [
      f.key,
      value[f.key] ?? (f.key === "organization_type" ? "UNKNOWN" : ""),
    ]),
  ) as PartyProfile;
}
