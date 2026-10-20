<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
const props = defineProps<{ category: string }>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
}>();
interface Rule {
  category: string;
  enabled: boolean;
  prefix: string;
  date_format: string;
  digits: number;
  next_number: number;
  version: string;
  example: string;
  history: { created_at: string; detail: { reason: string } }[];
}
const data = ref<Rule | null>(null),
  loading = ref(false),
  reason = ref("");
const form = ref({
  enabled: true,
  prefix: "",
  date_format: "NONE",
  digits: 6,
  start_number: 1,
});
const baseline = ref("");
const dirty = computed(
  () =>
    !!data.value &&
    (JSON.stringify(form.value) !== baseline.value || !!reason.value),
);
const { busy, execute } = useCommand((e) => emit("failure", e));
watch(
  () => dirty.value || busy.value || loading.value,
  (v) => emit("editing", v),
);
onUnmounted(
  registerCatalogChanges(() => ({ dirty: dirty.value, busy: busy.value })),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const r = await request<Rule>(
      `/api/v1/catalog/code-rules/${props.category}`,
    );
    if (current !== epoch) return;
    data.value = r;
    form.value = {
      enabled: r.enabled,
      prefix: r.prefix,
      date_format: r.date_format,
      digits: r.digits,
      start_number: r.next_number,
    };
    baseline.value = JSON.stringify(form.value);
    reason.value = "";
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
const example = computed(() => {
  const now = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  })
    .format(new Date())
    .replace(/\D/g, "");
  const length =
    { NONE: 0, YYYY: 4, YYYYMM: 6, YYYYMMDD: 8 }[form.value.date_format] || 0;
  return (
    form.value.prefix +
    now.slice(0, length) +
    String(form.value.start_number).padStart(
      Math.min(12, Math.max(1, form.value.digits || 1)),
      "0",
    )
  );
});
async function save() {
  if (!data.value) return;
  await execute(
    `/api/v1/catalog/code-rules/${props.category}`,
    {
      expected_version: data.value.version,
      ...form.value,
      reason: reason.value,
    },
    load,
  );
}
onMounted(load);
</script>
<template>
  <section class="panel" aria-label="商品自动编码规则" v-loading="loading">
    <h3>{{ category }} · 自动编码规则</h3>
    <p class="form-note">
      商品编码留空时，在保存成功时自动生成；手工填写时按原值保存。流水号持续递增，不按日期重置，旧商品编码保持不变。
    </p>
    <form v-if="data" class="business-form" @submit.prevent="save">
      <fieldset
        class="form-grid"
        :disabled="busy || loading || !can('catalog.configure')"
      >
        <label
          >启用自动编码<select v-model="form.enabled">
            <option :value="true">启用</option>
            <option :value="false">停用，必须手工填写</option>
          </select></label
        >
        <label
          >编码前缀<input
            v-model="form.prefix"
            maxlength="12"
            pattern="[A-Za-z0-9_\-]*"
            placeholder="例如 YP、QX-"
        /></label>
        <label
          >日期段<select v-model="form.date_format">
            <option value="NONE">无日期</option>
            <option value="YYYY">年</option>
            <option value="YYYYMM">年月</option>
            <option value="YYYYMMDD">年月日</option>
          </select></label
        >
        <label
          >流水位数<input
            v-model.number="form.digits"
            type="number"
            required
            min="1"
            max="12"
            step="1"
        /></label>
        <label
          >下次流水起始号<input
            v-model.number="form.start_number"
            type="number"
            required
            :min="data.next_number"
            max="999999999999"
            step="1"
        /></label>
        <p class="form-note">
          示例：<b>{{ example }}</b
          >。示例不占号；实际保存遇到已用编码会跳过，数字超出位数时不会截断。
        </p>
        <label class="full-width"
          >编码规则变更说明<input v-model="reason" required maxlength="200"
        /></label>
      </fieldset>
      <div class="form-actions">
        <el-button :disabled="busy || loading" @click="load"
          >重新加载编码规则</el-button
        ><el-button
          v-if="can('catalog.configure')"
          native-type="submit"
          type="primary"
          :disabled="!dirty || loading"
          :loading="busy"
          >保存编码规则</el-button
        >
      </div>
    </form>
    <details v-if="data?.history.length">
      <summary>最近规则变更</summary>
      <p v-for="(h, i) in data.history" :key="i">
        {{ new Date(h.created_at).toLocaleString() }} · {{ h.detail.reason }}
      </p>
    </details>
  </section>
</template>
<style scoped>
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
}
h3 {
  margin-top: 0;
}
</style>
