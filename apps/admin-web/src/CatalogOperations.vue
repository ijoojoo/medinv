<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, labels, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import { submitCatalogForm } from "./catalogInteraction";
import {
  operationalNames,
  type CatalogDictionary,
} from "./catalogDictionaries";
import DictionaryValue from "./DictionaryValue.vue";
import ProductFields from "./ProductFields.vue";
import type { ProductFieldPresentation } from "./productFieldRules";
const props = defineProps<{
  kind: "products" | "parties";
  target: string;
  active: boolean;
  masterVersion: string;
  hideSearchAlias?: boolean;
}>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
  saved: [];
}>();
interface Data {
  version: string;
  state: string;
  values: Record<string, string>;
  field_policy: ProductFieldPresentation | null;
}
const data = ref<Data | null>(null),
  dictionaries = ref<CatalogDictionary[]>([]),
  values = ref<Record<string, string>>({});
const reason = ref(""),
  loading = ref(false);
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () =>
    !!data.value &&
    (JSON.stringify(values.value) !== JSON.stringify(data.value.values) ||
      !!reason.value),
);
const fields = computed(() =>
  Object.keys(operationalNames).filter(
    (k) =>
      props.kind === "parties" ||
      ![
        ...(data.value?.field_policy || props.hideSearchAlias
          ? ["search_alias"]
          : []),
        "short_name",
        "contact_name",
        "contact_phone",
        "delivery_address",
      ].includes(k),
  ),
);
const groups = computed(() =>
  props.kind === "parties"
    ? [
        {
          title: "联系与收发货",
          note: "用于日常业务联系；默认地址不替代注册地址与经营地址。",
          keys: ["contact_name", "contact_phone", "delivery_address"],
        },
        {
          title: "名称与检索",
          note: "简称、别名与助记码用于检索，单位正式名称仍以基本信息为准。",
          keys: ["short_name", "search_alias"],
        },
        {
          title: "分类与推荐",
          note: "ABC 用于人工分类；需要双 ABC 时再填写第二维，分类口径由企业确定。",
          keys: ["recommendation_level", "abc_class", "abc_secondary_class"],
        },
        { title: "业务备注", note: "", keys: ["business_note"] },
      ]
    : [{ title: "", note: "", keys: fields.value }],
);
const form = ref<HTMLFormElement | null>(null);
const requestedFocus = ref("");
function focusField(key: string) {
  if (fields.value.includes(key)) requestedFocus.value = key;
}
watch(
  [requestedFocus, loading, busy, () => props.active],
  () => {
    if (!props.active) {
      requestedFocus.value = "";
      return;
    }
    if (!requestedFocus.value || loading.value || busy.value) return;
    const input = form.value?.querySelector<HTMLInputElement>(
      `[data-operation-field="${requestedFocus.value}"] input:not(:disabled), [data-operation-field="${requestedFocus.value}"] textarea:not(:disabled)`,
    );
    requestedFocus.value = "";
    if (input && !input.matches(":disabled")) input.focus();
  },
  { flush: "post" },
);
defineExpose({ focusField });
const base = computed(
  () => `/api/v1/catalog/${props.kind}/${props.target}/operations`,
);
function dictionary(key: string) {
  return dictionaries.value.find(
    (d) => d.key === (key.startsWith("abc_") ? "abc_class" : key),
  );
}
function capacity(key: string) {
  return key === "business_note"
    ? 500
    : key === "delivery_address"
      ? 300
      : key === "contact_phone"
        ? 50
        : key === "search_alias" && props.kind === "products"
          ? 200
          : 100;
}
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    props.kind,
  ),
);
watch([dirty, busy], () => emit("editing", dirty.value || busy.value));
let epoch = 0;
onUnmounted(() => {
  epoch++;
});
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const [record, dict] = await Promise.all([
      request<Data>(base.value),
      request<{ items: CatalogDictionary[] }>("/api/v1/catalog/dictionaries"),
    ]);
    if (current !== epoch) return;
    data.value = record;
    values.value = { ...record.values };
    dictionaries.value = dict.items;
    reason.value = "";
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function reset() {
  if (data.value) values.value = { ...data.value.values };
  reason.value = "";
}
async function save() {
  if (!data.value) return;
  const changes = Object.fromEntries(
    Object.entries(values.value).filter(
      ([k, v]) => v !== data.value!.values[k],
    ),
  );
  await execute(
    base.value,
    {
      expected_version: data.value.version,
      values: changes,
      reason: reason.value,
    },
    async () => {
      await load();
      emit("saved");
    },
  );
}
watch([() => props.active, () => props.masterVersion], () => {
  if (
    props.active &&
    !dirty.value &&
    !busy.value &&
    data.value?.version !== props.masterVersion
  )
    void load();
});
onMounted(load);
</script>
<template>
  <section
    aria-label="经营信息卡片"
    class="operations-card"
    v-loading="loading"
  >
    <div class="business-toolbar">
      <h3>经营信息</h3>
      <el-tag v-if="data">{{ labels[data.state] }}</el-tag
      ><span class="form-note">直接保存 · 不改变资质审核状态</span>
      <el-button
        :disabled="busy || loading || dirty"
        @click="load().then(() => emit('saved'))"
        >刷新经营信息</el-button
      >
    </div>
    <p v-if="kind === 'products'" class="form-note">
      ABC 用于人工分类；需要双 ABC 时再填写第二维。两维的业务口径由企业确定。
    </p>
    <form
      v-if="data"
      ref="form"
      class="business-form"
      @submit.prevent="save"
      @keydown="submitCatalogForm"
    >
      <fieldset
        :class="{
          'party-operations': kind === 'parties',
          'form-grid': kind === 'products',
        }"
        :disabled="busy || loading || !can('catalog.write')"
      >
        <ProductFields
          v-if="data.field_policy && !hideSearchAlias"
          v-model="values"
          :context-values="data.field_policy.context"
          :fields="
            data.field_policy.fields.filter((f) => f.key === 'search_alias')
          "
        />
        <section
          v-for="group in groups"
          :key="group.title"
          :aria-label="group.title || undefined"
          :class="
            kind === 'parties' ? 'operation-group' : 'product-operation-fields'
          "
        >
          <h4 v-if="group.title">{{ group.title }}</h4>
          <p v-if="group.note" class="form-note">{{ group.note }}</p>
          <div class="form-grid">
            <label
              v-for="key in group.keys"
              :key="key"
              :data-operation-field="key"
              :class="{
                'full-width':
                  key === 'business_note' || key === 'delivery_address',
              }"
            >
              {{ operationalNames[key] }}
              <DictionaryValue
                v-if="dictionary(key)"
                v-model="values[key]"
                :dictionary="dictionary(key)!"
                :label="operationalNames[key]"
              />
              <textarea
                v-else-if="['business_note', 'delivery_address'].includes(key)"
                v-model="values[key]"
                :aria-label="operationalNames[key]"
                :maxlength="capacity(key)"
                rows="2"
              />
              <input
                v-else
                v-model="values[key]"
                :aria-label="operationalNames[key]"
                :maxlength="capacity(key)"
                :type="key === 'contact_phone' ? 'tel' : 'text'"
                :pattern="
                  key === 'contact_phone'
                    ? '[0-9+\\(\\)（） .转分机\\-]*'
                    : undefined
                "
              />
            </label>
          </div>
        </section>
        <label v-if="can('catalog.write')" class="full-width"
          >变更说明<input
            v-model="reason"
            required
            maxlength="200"
            placeholder="简要说明调整原因，便于追查"
        /></label>
      </fieldset>
      <div class="form-actions" v-if="can('catalog.write')">
        <span class="form-note"
          >{{
            dirty ? "尚未保存；切换卡片会保留编辑内容。" : "已与当前档案同步"
          }}
          · Ctrl / ⌘ + S 保存</span
        >
        <el-button :disabled="busy || loading || !dirty" @click="reset"
          >撤销经营修改</el-button
        >
        <el-button
          type="primary"
          native-type="submit"
          :loading="busy"
          :disabled="
            loading ||
            !data ||
            JSON.stringify(values) === JSON.stringify(data.values)
          "
          >保存经营信息</el-button
        >
      </div>
    </form>
    <el-button v-else-if="!loading" @click="load">重新加载经营信息</el-button>
  </section>
</template>
<style scoped>
h3 {
  margin: 0;
  font-size: 16px;
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.operations-card {
  padding: 16px 0;
}
.form-actions {
  border-top: 1px solid var(--line);
  padding-top: 16px;
}
.party-operations {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.product-operation-fields,
.product-operation-fields > .form-grid {
  display: contents;
}
.operation-group {
  min-width: 0;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  padding: 14px;
}
.operation-group h4 {
  margin: 0 0 10px;
  font-size: 15px;
  color: var(--ui-accent);
}
.operation-group .form-note {
  margin: 0 0 12px;
}
.operation-group .form-grid {
  gap: 12px;
}
@media (max-width: 800px) {
  .party-operations {
    grid-template-columns: 1fr;
  }
}
</style>
