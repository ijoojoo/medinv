<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { ElAutocomplete } from "element-plus";
import { request } from "./api";
import { roleFor, type MasterValues } from "./productMaster";
const props = defineProps<{
  id: string;
  field: string;
  profile: string;
  values: MasterValues;
  disabled?: boolean;
  detailsDisabled?: boolean;
}>();
const emit = defineEmits<{ change: [value: unknown]; manage: [] }>();
type Entry = Record<string, any>;
interface Candidate extends Entry {
  value: string;
  description: string;
}
const isCertificate = computed(() => props.field === "product_certificates");
const entries = computed<Entry[]>(() =>
  Array.isArray(props.values[props.field])
    ? (props.values[props.field] as Entry[])
    : [],
);
const index = computed(() =>
  isCertificate.value
    ? Math.max(
        0,
        entries.value.findIndex((e) => e.business_use),
      )
    : 0,
);
const current = computed(() => entries.value[index.value]);
const text = computed(() =>
  String(
    isCertificate.value
      ? current.value?.number || current.value?.snapshot?.number || ""
      : current.value?.organization_name || "",
  ),
);
const role = computed(() =>
  props.field === "manufacturer"
    ? "MANUFACTURER"
    : props.field === "service_provider"
      ? "SERVICE_PROVIDER"
      : roleFor(props.profile, props.values),
);
const types: Record<string, string> = {
  DRUG_MAH: "DRUG_APPROVAL",
  DEVICE_FILER: "DEVICE_FILING",
  DEVICE_REGISTRANT: "DEVICE_REGISTRATION",
  HEALTH_REGISTRANT: "HEALTH_REGISTRATION",
  HEALTH_FILER: "HEALTH_FILING",
  FSMP_REGISTRANT: "FSMP_REGISTRATION",
  INFANT_REGISTRANT: "INFANT_FORMULA_REGISTRATION",
  COSMETIC_FILER: "COSMETIC_FILING",
  COSMETIC_REGISTRANT: "COSMETIC_REGISTRATION",
};
const error = ref("");
let epoch = 0;
onUnmounted(() => epoch++);
async function suggest(query: string, callback: (items: Candidate[]) => void) {
  const token = ++epoch;
  error.value = "";
  try {
    const kind = isCertificate.value ? "certificates" : "organizations";
    const result = await request<{ items: Entry[] }>(
      `/api/v1/catalog/master/${kind}?search=${encodeURIComponent(query.trim())}`,
    );
    if (token !== epoch) return;
    const items = result.items.filter((e) =>
      isCertificate.value
        ? e.certificate_type ===
          types[roleFor(props.profile, props.values) || ""]
        : e.kind === "ORGANIZATION",
    );
    callback(
      items.slice(0, 30).map((e) => ({
        ...e,
        value: isCertificate.value ? e.number : e.name,
        description: isCertificate.value
          ? `${e.holder_name || "持有人待补充"} · ${e.state === "VERIFIED" ? "已核验" : "待核验"} · ${e.id.slice(0, 8)}`
          : `${e.legal_code || "标识待补充"} · ${e.id.slice(0, 8)}`,
      })),
    );
  } catch {
    if (token !== epoch) return;
    callback([]);
    error.value = "候选暂不可用，输入已保留；保存时重新匹配";
  }
}
function replace(value?: Entry) {
  const next = [...entries.value];
  if (value) next.splice(index.value, current.value ? 1 : 0, value);
  else next.splice(index.value, 1);
  emit("change", next);
}
function input(raw: string | number) {
  const value = String(raw);
  if (value === text.value) return;
  if (!value) {
    replace();
    return;
  }
  replace(
    isCertificate.value
      ? {
          number: value,
          business_use:
            current.value?.business_use ??
            !entries.value.some((e) => e.business_use),
        }
      : {
          organization_name: value,
          role_code: current.value?.role_code || role.value,
          evidence_ref: "",
        },
  );
}
function choose(item: Entry) {
  replace(
    isCertificate.value
      ? {
          certificate_id: item.id,
          certificate_version: Number(item.version),
          snapshot: { number: item.number },
          business_use:
            current.value?.business_use ??
            !entries.value.some((e) => e.business_use),
        }
      : {
          organization_id: item.id,
          organization_name: item.name,
          role_code: current.value?.role_code || role.value,
          evidence_ref: "",
        },
  );
}
</script>
<template>
  <div
    class="master-reference"
    :title="error || '直接输入，保存时自动匹配或添加；↑↓选择候选，Enter确认'"
  >
    <el-autocomplete
      :id="id"
      :model-value="text"
      :disabled="disabled"
      :fetch-suggestions="suggest"
      :debounce="180"
      :maxlength="isCertificate ? 120 : 200"
      :placeholder="
        isCertificate
          ? '输入批准 / 注册 / 备案编号'
          : '输入名称，自动匹配或新增'
      "
      :fit-input-width="false"
      popper-class="master-reference-candidates"
      @input="input"
      @select="choose"
    >
      <template #default="{ item }"
        ><div>{{ item.value }}</div>
        <small>{{ item.description }}</small></template
      >
    </el-autocomplete>
    <button
      type="button"
      class="master-reference-more"
      :aria-label="isCertificate ? '证件详情与核验' : '更多组织关系'"
      :disabled="detailsDisabled ?? disabled"
      @click="emit('manage')"
    >
      {{ entries.length > 1 ? `+${entries.length - 1}` : "⋯" }}
    </button>
  </div>
</template>
<style>
.master-reference {
  display: flex;
  min-width: 0;
  height: 36px;
  gap: 4px;
}
.master-reference .el-autocomplete {
  flex: 1;
  min-width: 0;
}
.master-reference .el-input__wrapper {
  height: 36px;
  box-sizing: border-box;
  padding: 0 10px;
}
.master-reference input.el-input__inner {
  border: 0;
  height: 34px;
  padding: 0;
  outline: 0;
}
.master-reference-more {
  width: 30px;
  flex: none;
  border: 1px solid #ccd8e5;
  border-radius: 5px;
  color: #246bb2;
  background: #f4f8fc;
  cursor: pointer;
}
.master-reference-candidates {
  min-width: 340px;
  max-width: min(580px, 90vw);
}
.master-reference-candidates li {
  line-height: 23px !important;
  padding-top: 7px !important;
  padding-bottom: 7px !important;
}
.master-reference-candidates small {
  color: #75859a;
}
</style>
