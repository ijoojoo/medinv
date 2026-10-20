export interface ProductProfile {
  drug_business_scope: string;
  device_business_scope: string;
  quality_note: string;
  search_alias: string;
  trade_name: string;
  storage_conditions: string;
  manufacturer_address: string;
  generic_name: string;
  dosage_form: string;
  authorization_holder: string;
  authorization_holder_address: string;
  device_class: string;
  device_model: string;
  registrant_name: string;
  registrant_address: string;
}
export const emptyProductProfile = (): ProductProfile => ({
  drug_business_scope: "",
  device_business_scope: "",
  quality_note: "",
  search_alias: "",
  trade_name: "",
  storage_conditions: "",
  manufacturer_address: "",
  generic_name: "",
  dosage_form: "",
  authorization_holder: "",
  authorization_holder_address: "",
  device_class: "UNKNOWN",
  device_model: "",
  registrant_name: "",
  registrant_address: "",
});
type Field = {
  key: keyof ProductProfile;
  label: string;
  max: number;
  wide?: boolean;
};
export const commonProfileFields: Field[] = [
  { key: "quality_note", label: "质量管控备注", max: 500, wide: true },
  { key: "search_alias", label: "检索别名 / 助记码", max: 200 },
  { key: "trade_name", label: "商品名 / 品牌", max: 100 },
  { key: "manufacturer_address", label: "生产企业地址", max: 300, wide: true },
  {
    key: "storage_conditions",
    label: "储存条件（按标签 / 说明书）",
    max: 500,
    wide: true,
  },
];
export const drugProfileFields: Field[] = [
  { key: "drug_business_scope", label: "商品经营范围类别（药品）", max: 100 },
  { key: "generic_name", label: "药品通用名", max: 200 },
  { key: "dosage_form", label: "剂型", max: 100 },
  {
    key: "authorization_holder",
    label: "上市许可持有人",
    max: 200,
    wide: true,
  },
  {
    key: "authorization_holder_address",
    label: "上市许可持有人地址",
    max: 300,
    wide: true,
  },
];
export const deviceProfileFields: Field[] = [
  { key: "device_business_scope", label: "商品经营范围类别（器械）", max: 100 },
  { key: "device_model", label: "器械型号", max: 200 },
  { key: "registrant_name", label: "注册人 / 备案人", max: 200, wide: true },
  {
    key: "registrant_address",
    label: "注册人 / 备案人地址",
    max: 300,
    wide: true,
  },
];
export const deviceClasses: Record<string, string> = {
  UNKNOWN: "待核定",
  I: "第一类",
  II: "第二类",
  III: "第三类",
};
export const profileFieldNames: Record<string, string> = Object.fromEntries(
  [...commonProfileFields, ...drugProfileFields, ...deviceProfileFields].map(
    (f) => [f.key, f.label],
  ),
);
profileFieldNames.device_class = "器械管理类别";
export const qualityProfileKeys = new Set([
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
]);
export function readProductProfile(
  record: Partial<ProductProfile>,
): ProductProfile {
  const result = emptyProductProfile();
  for (const key of Object.keys(result) as (keyof ProductProfile)[])
    result[key] = record[key] ?? result[key];
  return result;
}
export function profileForCategory(
  profile: ProductProfile,
  category: string,
): ProductProfile {
  const result = { ...profile };
  if (category === "DRUG") {
    result.device_class = "UNKNOWN";
    for (const field of deviceProfileFields) result[field.key] = "";
  } else {
    for (const field of drugProfileFields) result[field.key] = "";
  }
  return result;
}
