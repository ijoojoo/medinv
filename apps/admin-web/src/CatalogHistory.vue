<script setup lang="ts">
import AttachmentLink from "./AttachmentLink.vue";
import { session } from "./api";
import { localTime } from "./sales";
import { deviceClasses, profileFieldNames } from "./productProfile";
import { partyFieldNames, organizationTypes } from "./partyProfile";
import { operationalNames } from "./catalogDictionaries";
import { certificateNames, certificateValue } from "./productCertificates";
import {
  partyCertificateNames,
  partyCertificateValue,
} from "./partyCertificates";
export interface CatalogEvent {
  id: string;
  created_by: string;
  created_at: string;
  action: string;
  detail: Record<string, any>;
}
defineProps<{
  events: CatalogEvent[];
  attachmentArea?: "products" | "parties";
  attachmentParent?: string;
}>();
const names: Record<string, string> = {
  ...profileFieldNames,
  ...partyFieldNames,
  ...operationalNames,
  name: "名称",
  manufacturer_name: "生产企业",
  specification: "规格型号",
  approval_number: "批准 / 注册 / 备案编号",
  valid_from: "资质生效日期",
  valid_until: "资质截止日期",
  drug_sale_class: "药品销售分类",
  license_no: "经营许可 / 备案编号",
  categories: "经营类别",
  kind: "业务身份",
  purchase_price: "参考采购价",
  wholesale_price: "参考批发价",
  retail_price: "建议零售价",
  note: "价格备注",
};
function value(
  v: any,
  key?: string,
  certificate = false,
  party = false,
): string {
  if (certificate && party) return partyCertificateValue(v, key || "");
  if (certificate) return certificateValue(v, key || "");
  if (key === "organization_type")
    return organizationTypes[String(v)] || String(v || "未填写");
  if (key === "device_class")
    return deviceClasses[String(v)] || String(v || "未填写");
  if (Array.isArray(v)) return v.map((item) => value(item)).join("、");
  return (
    (
      {
        DRUG: "药品",
        DEVICE: "医疗器械",
        SUPPLIER: "供应商",
        CUSTOMER: "客户",
        BOTH: "供应商及客户",
        UNKNOWN: "待核定",
        OTC: "非处方药 OTC",
        RX: "处方药 RX",
      } as Record<string, string>
    )[String(v)] || String(v || "未填写")
  );
}
function changes(event: CatalogEvent) {
  const d = event.detail;
  return Object.keys(d.after || {}).filter(
    (k) => JSON.stringify(d.before?.[k]) !== JSON.stringify(d.after[k]),
  );
}
</script>
<template>
  <section class="catalog-history" data-testid="catalog-history">
    <h3>资料变更记录</h3>
    <p v-if="!events.length" class="form-note">
      暂无记录（旧种子资料可能没有建档记录）。
    </p>
    <details
      v-for="event in events"
      :key="event.id"
      :open="
        [
          'catalog.revised',
          'catalog.business_updated',
          'catalog.prices_updated',
        ].includes(event.action)
      "
    >
      <summary>
        {{ localTime(event.created_at) }} ·
        {{
          event.action === "catalog.certificate_created"
            ? "证照建档"
            : event.action === "catalog.certificate_revised"
              ? "证照修订（待重新核验）"
              : event.action === "catalog.certificate_reviewed"
                ? event.detail.decision === "VERIFY"
                  ? "证照核验通过"
                  : "证照停用"
                : event.action === "catalog.prices_updated"
                  ? "参考价格更新（无需资质审核）"
                  : event.action === "catalog.revised"
                    ? "资料修订"
                    : event.action === "catalog.business_updated"
                      ? "经营信息更新（无需审核）"
                      : event.action === "catalog.reviewed"
                        ? event.detail.decision === "APPROVE"
                          ? "审核通过"
                          : "停用资料"
                        : "资料建档"
        }}
        · {{ event.created_by === session?.principal_id ? "本人" : "其他成员" }}
      </summary>
      <p v-if="event.detail.reason">{{ event.detail.reason }}</p>
      <p v-if="event.detail.evidence_ref">
        核验凭据：{{ event.detail.evidence_ref }}
      </p>
      <table
        v-if="
          [
            'catalog.revised',
            'catalog.business_updated',
            'catalog.prices_updated',
            'catalog.certificate_created',
            'catalog.certificate_revised',
            'catalog.certificate_reviewed',
          ].includes(event.action)
        "
      >
        <thead>
          <tr>
            <th>字段</th>
            <th>修订前</th>
            <th>修订后</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="key in changes(event)" :key="key">
            <td>
              {{
                (event.action.startsWith("catalog.certificate_")
                  ? (event.detail.certificate_domain === "party"
                      ? partyCertificateNames
                      : certificateNames)[key]
                  : names[key]) || key
              }}
            </td>
            <td>
              <AttachmentLink
                v-if="
                  key === 'attachment_id' &&
                  event.detail.before[key] &&
                  attachmentArea &&
                  attachmentParent
                "
                :area="attachmentArea"
                :parent="attachmentParent"
                :id="event.detail.before[key]"
              />
              {{
                value(
                  event.detail.before[key],
                  key,
                  event.action.startsWith("catalog.certificate_"),
                  event.detail.certificate_domain === "party",
                )
              }}
            </td>
            <td>
              <AttachmentLink
                v-if="
                  key === 'attachment_id' &&
                  event.detail.after[key] &&
                  attachmentArea &&
                  attachmentParent
                "
                :area="attachmentArea"
                :parent="attachmentParent"
                :id="event.detail.after[key]"
              />
              {{
                value(
                  event.detail.after[key],
                  key,
                  event.action.startsWith("catalog.certificate_"),
                  event.detail.certificate_domain === "party",
                )
              }}
            </td>
          </tr>
        </tbody>
      </table>
      <small>操作人 ID：{{ event.created_by }}</small>
    </details>
  </section>
</template>
<style scoped>
.catalog-history {
  margin-top: 24px;
}
details {
  border-top: 1px solid var(--line, #e5e9e7);
  padding: 12px 0;
}
summary {
  cursor: pointer;
  line-height: 1.6;
}
table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  margin: 12px 0;
}
th,
td {
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid #e5e9e7;
  overflow-wrap: anywhere;
  vertical-align: top;
}
small {
  overflow-wrap: anywhere;
}
</style>
