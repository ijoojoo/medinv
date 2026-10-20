<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
const props = defineProps<{ sku: string; code: string; active: boolean }>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
  saved: [];
}>();
const names = {
  purchase_price: "参考采购价",
  wholesale_price: "参考批发价",
  retail_price: "建议零售价",
};
type Values = Record<keyof typeof names, string | null> & { note: string };
const data = ref<{ version: string; base_unit: string; values: Values } | null>(
  null,
);
const values = ref<Values>({
  purchase_price: null,
  wholesale_price: null,
  retail_price: null,
  note: "",
});
const loading = ref(false),
  reason = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () =>
    !!data.value &&
    (JSON.stringify(values.value) !== JSON.stringify(data.value.values) ||
      !!reason.value),
);
watch(
  () => dirty.value || busy.value,
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
    const r = await request<NonNullable<typeof data.value>>(
      `/api/v1/catalog/skus/${props.sku}/prices`,
    );
    if (current !== epoch) return;
    data.value = r;
    values.value = { ...r.values };
    reason.value = "";
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
watch(
  () => props.active,
  (active) => {
    if (active && !dirty.value && !busy.value) void load();
  },
);
async function save() {
  if (!data.value) return;
  await execute(
    `/api/v1/catalog/skus/${props.sku}/prices`,
    {
      expected_version: data.value.version,
      ...values.value,
      ...Object.fromEntries(
        Object.keys(names).map((k) => [
          k,
          values.value[k as keyof typeof names] || null,
        ]),
      ),
      reason: reason.value,
    },
    async () => {
      await load();
      emit("saved");
    },
  );
}
onMounted(load);
</script>
<template>
  <section
    class="catalog-price-card"
    aria-label="价格信息卡片"
    v-loading="loading"
  >
    <h3>参考价格 · 元／{{ data?.base_unit || "基本单位" }}</h3>
    <p class="form-note">
      本企业参考价，人民币、含税、每
      {{ data?.base_unit || "基本单位" }}。留空表示未设置，0
      表示零价。当前用于资料维护，单据成交价仍需在开单时确认；不改写库存成本和历史价格。
    </p>
    <form v-if="data" class="business-form" @submit.prevent="save">
      <fieldset
        class="form-grid"
        :disabled="busy || loading || !can('catalog.write')"
      >
        <label v-for="(name, key) in names" :key="key"
          >{{ name
          }}<input
            v-model="values[key]"
            :aria-label="name"
            inputmode="decimal"
            pattern="(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?"
            placeholder="未设置"
        /></label>
        <label class="full-width"
          >价格备注<textarea v-model="values.note" maxlength="500" rows="2" />
        </label>
        <label v-if="can('catalog.write')" class="full-width"
          >价格变更说明<input v-model="reason" required maxlength="200"
        /></label>
      </fieldset>
      <div class="form-actions">
        <span class="form-note">{{
          dirty ? "尚未保存；切换卡片保留编辑。" : "参考价格已同步"
        }}</span
        ><el-button :disabled="busy || loading" @click="load"
          >重新加载价格</el-button
        ><el-button
          v-if="can('catalog.write')"
          native-type="submit"
          type="primary"
          :disabled="!dirty || loading"
          :loading="busy"
          >保存价格信息</el-button
        >
      </div>
    </form>
  </section>
</template>
<style scoped>
fieldset {
  border: 0;
  margin: 0;
  padding: 0;
}
.catalog-price-card {
  padding: 16px 0;
}
</style>
