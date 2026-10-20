<script setup lang="ts">
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
const props = defineProps<{ product: string }>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
}>();
interface Values {
  enabled: boolean;
  purchase_enabled: boolean;
  sales_enabled: boolean;
  recommendation_level: string;
  abc_class: string;
  abc_secondary_class: string;
  business_note: string;
}
const entity = ref(activeEntity.value),
  version = ref(0),
  inherited = ref(true),
  baseline = ref(""),
  reason = ref("维护经营主体设置");
const values = ref<Values>({
  enabled: true,
  purchase_enabled: true,
  sales_enabled: true,
  recommendation_level: "",
  abc_class: "",
  abc_secondary_class: "",
  business_note: "",
});
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(() => JSON.stringify(values.value) !== baseline.value);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    "products",
    async () => {
      await save();
      return !dirty.value && !busy.value;
    },
  ),
);
onMounted(load);
async function load() {
  if (!entity.value) return;
  try {
    const r = await request<{
      version: number;
      inherited: boolean;
      values: Values;
    }>(
      `/api/v1/catalog/master/products/${props.product}/entity-settings?entity_id=${entity.value}`,
    );
    values.value = r.values;
    version.value = r.version;
    inherited.value = r.inherited;
    baseline.value = JSON.stringify(values.value);
    emit("editing", false);
  } catch (e) {
    emit("failure", e);
  }
}
async function save() {
  const snapshot = JSON.stringify(values.value);
  await execute<{ version: number }>(
    `/api/v1/catalog/master/products/${props.product}/entity-settings`,
    {
      entity_id: entity.value,
      expected_version: version.value,
      ...JSON.parse(snapshot),
      reason: reason.value,
    },
    (result) => {
      version.value = result.version;
      inherited.value = false;
      baseline.value = snapshot;
      emit("editing", dirty.value);
    },
  );
}
</script>
<template>
  <section class="entity-product-settings">
    <div class="master-search">
      <span
        >{{
          inherited
            ? "尚未独立设置，推荐与分类取企业默认值"
            : "当前主体的独立经营设置"
        }}
        · v{{ version }}</span
      >
    </div>
    <div
      class="master-fixed-grid"
      @input="emit('editing', true)"
      @change="emit('editing', true)"
    >
      <label
        ><input type="checkbox" v-model="values.enabled" />
        在当前主体启用</label
      ><label
        ><input type="checkbox" v-model="values.purchase_enabled" />
        允许采购</label
      ><label
        ><input type="checkbox" v-model="values.sales_enabled" />
        允许销售</label
      ><label
        >推荐级别
        <input
          v-model="values.recommendation_level"
          placeholder="按商品字典填写"
          maxlength="60" /></label
      ><label
        >ABC 分类 <input v-model="values.abc_class" maxlength="60" /></label
      ><label
        >第二维 ABC
        <input v-model="values.abc_secondary_class" maxlength="60" /></label
      ><label
        >经营说明 <input v-model="values.business_note" maxlength="500"
      /></label>
    </div>
    <div class="master-footer">
      <span>经营设置独立保存并审计，不撤销公共商品的质量审核。</span
      ><input v-model="reason" aria-label="经营设置说明" /><el-button
        type="primary"
        :disabled="!entity || !can('catalog.write')"
        :loading="busy"
        @click="save"
        >保存当前主体设置</el-button
      >
    </div>
  </section>
</template>
