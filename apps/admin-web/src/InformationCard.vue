<script setup lang="ts">
import { computed } from "vue";
import { labels } from "./business";
import { organizationTypes } from "./partyProfile";
const props = defineProps<{
  modelValue: boolean;
  kind: "product" | "party";
  record: Record<string, unknown>;
  legacy?: Record<string, unknown>;
  version?: string | number;
  state?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
const title = computed(() =>
  props.kind === "product"
    ? "商品信息卡"
    : ["SUPPLIER", "BOTH"].includes(String(props.record.kind))
      ? "供应商信息卡"
      : "客户信息卡",
);
const groups = computed(() =>
  props.kind === "product"
    ? [
        {
          name: "商品身份",
          fields: [
            ["name", "商品名称"],
            ["code", "商品编码"],
            ["generic_name", "通用名称"],
            ["trade_name", "商品名 / 品牌"],
            ["specification", "规格"],
            ["device_model", "型号"],
            ["dosage_form", "剂型"],
            ["base_unit", "基本单位"],
          ],
        },
        {
          name: "生产与资质",
          fields: [
            ["manufacturer", "生产企业"],
            ["regulatory_subject", "注册 / 许可主体"],
            ["product_certificates", "产品资质"],
            ["drug_business_scope", "药品经营范围类别"],
            ["device_business_scope", "器械经营范围类别"],
            ["storage_conditions", "储存条件"],
          ],
        },
        {
          name: "经营信息",
          fields: [
            ["recommendation_level", "推荐级别"],
            ["abc_class", "ABC 分类"],
            ["abc_secondary_class", "第二维 ABC"],
            ["business_note", "经营备注"],
          ],
        },
      ]
    : [
        {
          name: "单位身份",
          fields: [
            ["name", "单位名称"],
            ["code", "单位编码"],
            ["kind", "业务身份"],
            ["credit_code", "统一社会信用代码"],
            ["short_name", "简称"],
            ["organization_type", "机构性质"],
            ["legal_representative", "法定代表人 / 负责人"],
            ["registered_address", "注册地址"],
          ],
        },
        {
          name: "联系与经营",
          fields: [
            ["contact_name", "联系人"],
            ["contact_phone", "联系电话"],
            ["delivery_address", "默认收发货地址"],
            ["recommendation_level", "推荐级别"],
            ["abc_class", "ABC 分类"],
            ["business_note", "经营备注"],
          ],
        },
        {
          name: "资质摘要",
          fields: [
            ["license_no", "原始许可证号"],
            ["valid_from", "资质起始日期"],
            ["valid_until", "资质截止日期"],
            ["categories", "经营类别"],
          ],
        },
      ],
);
function display(value: unknown): string {
  if (value == null || value === "") return "未填写";
  if (Array.isArray(value)) return value.map(display).join("；") || "未填写";
  if (typeof value === "object") {
    const r = value as Record<string, unknown>;
    const snapshot = (r.snapshot || {}) as Record<string, unknown>;
    return String(
      r.organization_name ||
        r.holder_name ||
        r.number ||
        r.name ||
        r.code ||
        snapshot.number ||
        snapshot.certificate_number ||
        "详见关联资料",
    );
  }
  return (
    labels[String(value)] || organizationTypes[String(value)] || String(value)
  );
}
</script>
<template>
  <el-drawer
    :model-value="modelValue"
    :title="title"
    size="min(820px, 96vw)"
    @update:model-value="emit('update:modelValue', $event)"
    ><article class="information-card">
      <header>
        <span class="information-mark">{{
          kind === "product" ? "品" : "企"
        }}</span>
        <div>
          <h2>{{ record.name }}</h2>
          <p>
            {{ record.code }} · {{ labels[state || ""] || state }} · v{{
              version
            }}
          </p>
        </div>
      </header>
      <section v-for="group in groups" :key="group.name">
        <h3>{{ group.name }}</h3>
        <dl>
          <template v-for="field in group.fields" :key="field[0]"
            ><dt>{{ field[1] }}</dt>
            <dd>{{ display(record[field[0]]) }}</dd></template
          >
        </dl>
      </section>
      <section v-if="kind === 'product' && legacy">
        <h3>旧档原始记载</h3>
        <p class="form-note">{{ legacy.message }}</p>
        <dl>
          <template
            v-for="field in [
              ['manufacturer_name', '原生产企业名称'],
              ['authorization_holder', '原持有人名称'],
              ['registrant_name', '原注册人名称'],
              ['approval_number', '原批准 / 注册 / 备案编号'],
              ['valid_from', '原有效期起始'],
              ['valid_until', '原有效期截止'],
            ]"
            :key="field[0]"
          >
            <dt>{{ field[1] }}</dt>
            <dd>{{ display(legacy[field[0]]) }}</dd>
          </template>
        </dl>
      </section>
      <p class="form-note">
        信息卡显示已保存资料。完整证照、核验依据和变更记录可在原资料的质量管控页签查看。
      </p>
    </article></el-drawer
  >
</template>
<style scoped>
.information-card {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  overflow: hidden;
}
.information-card header {
  display: flex;
  gap: 16px;
  align-items: center;
  background: var(--ui-soft);
  padding: 24px;
}
.information-mark {
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--ui-accent);
  color: #fff;
  font-size: 24px;
}
.information-card h2 {
  margin: 0 0 8px;
  font-size: 21px;
}
.information-card header p {
  margin: 0;
  color: var(--ui-muted);
  font-size: 12px;
}
.information-card section {
  padding: 10px 24px;
}
.information-card h3 {
  font-size: 14px;
  border-bottom: 1px solid var(--ui-border);
  padding-bottom: 12px;
}
.information-card dl {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  gap: 10px 18px;
  font-size: 13px;
}
.information-card dt {
  color: var(--ui-muted);
}
.information-card dd {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.information-card > .form-note {
  padding: 12px 24px;
  background: var(--ui-tint);
  margin-bottom: 0;
}
@media (max-width: 500px) {
  .information-card dl {
    grid-template-columns: 95px minmax(0, 1fr);
  }
}
</style>
