<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, useId, watch } from "vue";
import { ElPopover } from "element-plus";
import { request } from "./api";
type Reference = {
  id: string;
  name: string;
  code: string;
  credit_code?: string;
  eligible: boolean;
  state?: string;
};
const props = withDefaults(
  defineProps<{
    modelValue?: string;
    kind: "party" | "warehouse";
    entity: string;
    channel: "purchase" | "sale";
    label: string;
    required?: boolean;
    disabled?: boolean;
    qualifiedOnly?: boolean;
  }>(),
  { qualifiedOnly: true },
);
const emit = defineEmits<{
  "update:modelValue": [id: string];
  selected: [party: Reference | null];
}>();
const warehouse = computed(() => props.kind === "warehouse");
const noun = computed(() => (warehouse.value ? "仓库" : "单位"));
const prefix = computed(() =>
  warehouse.value ? "warehouse-lookup" : "party-lookup",
);
function url(extra: Record<string, string>) {
  return `${warehouse.value ? "/api/v1/warehouses/lookup" : "/api/v1/catalog/entry-parties"}?${new URLSearchParams(
    {
      entity_id: props.entity,
      channel: props.channel,
      ...(!warehouse.value
        ? { qualified_only: String(props.qualifiedOnly) }
        : {}),
      ...extra,
    },
  )}`;
}
const uid = useId(),
  input = ref<HTMLInputElement>(),
  root = ref<HTMLElement>(),
  popup = ref<HTMLElement>();
const text = ref(""),
  chosen = ref<Reference | null>(null),
  rows = ref<Reference[]>([]),
  opened = ref(false),
  loading = ref(false),
  labelLoading = ref(false),
  error = ref(""),
  offset = ref(0),
  more = ref(false),
  index = ref(-1);
let epoch = 0,
  labelEpoch = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
let pendingSearch: Promise<boolean> | undefined;
let restoringFocus = false;
function validity() {
  input.value?.setCustomValidity(
    labelLoading.value
      ? `正在读取所选${noun.value}，请稍候`
      : props.modelValue &&
          (chosen.value?.id !== props.modelValue ||
            (warehouse.value && !chosen.value?.eligible))
        ? `所选${noun.value}不可用，请重新选择`
        : text.value && !props.modelValue
          ? `请从检索结果中选择已建档的${noun.value}`
          : "",
  );
}
function close() {
  opened.value = false;
  epoch++;
  clearTimeout(timer);
  timer = undefined;
  pendingSearch = undefined;
  loading.value = false;
  index.value = -1;
}
function search(next = 0): Promise<boolean> {
  const token = ++epoch;
  clearTimeout(timer);
  timer = undefined;
  loading.value = true;
  error.value = "";
  index.value = -1;
  const query =
    chosen.value && chosen.value.id === props.modelValue
      ? ""
      : text.value.trim();
  pendingSearch = (async () => {
    try {
      const result = await request<{ items: Reference[]; has_more: boolean }>(
        url({ search: query, offset: String(next) }),
      );
      if (token !== epoch || !opened.value) return false;
      rows.value = result.items;
      more.value = result.has_more;
      offset.value = next;
      // Refocusing an existing selection must not silently choose a different
      // first-page record. Arrow keys still let the user change the selection.
      index.value =
        chosen.value && chosen.value.id === props.modelValue && !query
          ? rows.value.findIndex((row) => row.id === props.modelValue)
          : rows.value.findIndex((row) => !props.qualifiedOnly || row.eligible);
      await nextTick();
      popup.value
        ?.querySelector(".reference-lookup-results")
        ?.scrollTo({ top: 0 });
      return true;
    } catch {
      if (token === epoch) {
        rows.value = [];
        more.value = false;
        error.value = "检索失败，输入已保留，请重试";
      }
      return false;
    } finally {
      if (token === epoch) loading.value = false;
    }
  })();
  return pendingSearch;
}
function show() {
  if (
    restoringFocus ||
    props.disabled ||
    !props.entity ||
    input.value?.matches(":disabled")
  )
    return;
  opened.value = true;
  void search();
}
function edit(event: Event) {
  labelEpoch++;
  labelLoading.value = false;
  text.value = (event.target as HTMLInputElement).value;
  chosen.value = null;
  emit("update:modelValue", "");
  emit("selected", null);
  epoch++;
  clearTimeout(timer);
  rows.value = [];
  more.value = false;
  error.value = "";
  index.value = -1;
  opened.value = true;
  loading.value = true;
  timer = setTimeout(() => void search(), 180);
  void nextTick(validity);
}
function choose(row: Reference) {
  if (
    props.disabled ||
    input.value?.matches(":disabled") ||
    (props.qualifiedOnly && !row.eligible)
  )
    return;
  labelEpoch++;
  labelLoading.value = false;
  chosen.value = row;
  text.value = row.name;
  emit("update:modelValue", row.id);
  emit("selected", row);
  close();
  void nextTick(() => {
    validity();
    restoringFocus = true;
    input.value?.focus();
    restoringFocus = false;
  });
}
function clear() {
  labelEpoch++;
  labelLoading.value = false;
  text.value = "";
  chosen.value = null;
  close();
  emit("update:modelValue", "");
  emit("selected", null);
  void nextTick(() => {
    validity();
    input.value?.focus();
  });
}
async function keys(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) {
    if (e.key === "Enter") e.preventDefault();
    e.stopPropagation();
    return;
  }
  if (props.disabled || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "Enter" && e.shiftKey) {
    close();
    return;
  }
  if (["ArrowDown", "ArrowUp"].includes(e.key)) {
    e.preventDefault();
    e.stopPropagation();
    if (!opened.value) {
      opened.value = true;
      await search();
      return;
    }
    if (timer || loading.value) {
      await (timer ? search() : pendingSearch);
      return;
    }
    if (rows.value.length)
      index.value =
        index.value < 0
          ? e.key === "ArrowDown"
            ? 0
            : rows.value.length - 1
          : (index.value +
              (e.key === "ArrowDown" ? 1 : -1) +
              rows.value.length) %
            rows.value.length;
    await nextTick();
    popup.value
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && opened.value) {
    e.preventDefault();
    e.stopPropagation();
    const typed = text.value,
      entity = props.entity,
      channel = props.channel;
    const ready = timer
      ? await search()
      : loading.value && pendingSearch
        ? await pendingSearch
        : error.value
          ? await search()
          : true;
    if (
      !ready ||
      !opened.value ||
      props.disabled ||
      text.value !== typed ||
      props.entity !== entity ||
      props.channel !== channel ||
      document.activeElement !== input.value
    )
      return;
    const row = rows.value[index.value];
    if (row && !loading.value) choose(row);
    else if (chosen.value && chosen.value.id === props.modelValue) close();
  } else if (e.key === "Escape" && opened.value) {
    e.preventDefault();
    e.stopPropagation();
    close();
  } else if (e.key === "Tab") close();
}
function outside(event: PointerEvent) {
  if (
    !root.value?.contains(event.target as Node) &&
    !popup.value?.contains(event.target as Node)
  )
    close();
}
document.addEventListener("pointerdown", outside);
watch(
  () => [props.entity, props.channel, props.modelValue] as const,
  async ([entity, channel, id], previous) => {
    const token = ++labelEpoch;
    if (previous && (entity !== previous[0] || channel !== previous[1])) {
      close();
      chosen.value = null;
      text.value = "";
    }
    if (!id) {
      labelLoading.value = false;
      if (chosen.value) text.value = "";
      chosen.value = null;
      await nextTick();
      validity();
      return;
    }
    if (chosen.value?.id === id) {
      labelLoading.value = false;
      validity();
      return;
    }
    labelLoading.value = true;
    await nextTick();
    validity();
    try {
      const result = await request<{ items: Reference[] }>(
        url({ [warehouse.value ? "warehouse_id" : "party_id"]: id }),
      );
      if (token !== labelEpoch) return;
      chosen.value = result.items[0] || null;
      text.value = chosen.value?.name || `所选${noun.value}不可用，请重新选择`;
      if (!chosen.value)
        input.value?.setCustomValidity(`所选${noun.value}不可用，请重新选择`);
      else validity();
    } catch {
      if (token === labelEpoch) {
        text.value = `所选${noun.value}读取失败，请重试`;
        input.value?.setCustomValidity(`请重新检索并选择${noun.value}`);
      }
    } finally {
      if (token === labelEpoch) {
        labelLoading.value = false;
        validity();
      }
    }
  },
  { immediate: true },
);
watch(
  () => props.disabled,
  (value) => {
    if (value) close();
  },
);
onUnmounted(() => {
  close();
  labelEpoch++;
  document.removeEventListener("pointerdown", outside);
});
const ready = computed(() => !labelLoading.value);
defineExpose({ ready });
</script>
<template>
  <span
    ref="root"
    class="reference-lookup"
    :class="prefix"
    :data-entity="entity"
    :data-channel="channel"
  >
    <ElPopover
      :visible="opened"
      :persistent="false"
      :width="620"
      placement="bottom-start"
      :show-arrow="false"
      :popper-options="{ strategy: 'fixed' }"
      :popper-class="'reference-lookup-popover ' + prefix + '-popover'"
    >
      <template #reference>
        <span class="reference-lookup-input">
          <input
            ref="input"
            role="combobox"
            :aria-label="label"
            aria-autocomplete="list"
            :aria-expanded="opened"
            :aria-controls="uid"
            :aria-activedescendant="
              opened && index >= 0 ? uid + '-' + index : undefined
            "
            :value="text"
            :required="required"
            :disabled="disabled"
            maxlength="100"
            :placeholder="
              warehouse
                ? '输入仓库名称 / 编码检索'
                : '输入名称 / 编码 / 信用代码检索'
            "
            autocomplete="off"
            @input="edit"
            @focus="show"
            @click="!opened && show()"
            @keydown="keys"
          />
          <button
            v-if="text"
            type="button"
            :aria-label="'清除' + label"
            :disabled="disabled"
            @click="clear"
          >
            ×
          </button>
        </span>
      </template>
      <div ref="popup" @keydown.esc.stop="close">
        <div class="reference-lookup-heading">
          <strong>{{ label }}检索</strong
          ><span>默认首条 · ↑↓ 切换 · Enter 确认 · Esc 关闭</span>
        </div>
        <p v-if="loading" role="status">正在检索…</p>
        <p v-else-if="error" role="alert">
          {{ error }} <button type="button" @click="search()">重试</button>
        </p>
        <div
          :id="uid"
          role="listbox"
          :aria-label="label + '检索结果'"
          class="reference-lookup-results"
        >
          <button
            v-for="(row, i) in rows"
            :id="uid + '-' + i"
            :key="row.id"
            type="button"
            role="option"
            :data-party="warehouse ? undefined : row.id"
            :data-warehouse="warehouse ? row.id : undefined"
            :disabled="loading || (qualifiedOnly && !row.eligible)"
            :aria-selected="index === i"
            @pointerdown.prevent
            @pointermove="index = i"
            @click="choose(row)"
          >
            <span
              ><b>{{ row.name }}</b
              ><small
                >{{ row.code
                }}<template v-if="!warehouse">
                  · {{ row.credit_code || "信用代码未填写" }}</template
                ></small
              ></span
            >
            <small>{{
              warehouse
                ? row.eligible
                  ? "实体仓库"
                  : "已停用"
                : row.eligible
                  ? "资质有效"
                  : "历史资料"
            }}</small>
          </button>
        </div>
        <p v-if="!loading && !error && !rows.length">
          未找到匹配{{ noun }}，请调整关键词{{
            !warehouse && qualifiedOnly ? "，或检查资料审核与有效期" : ""
          }}。
        </p>
        <div class="reference-lookup-footer">
          <span>每页20条 · 第 {{ offset / 20 + 1 }} 页</span
          ><button
            type="button"
            :disabled="loading || !offset"
            @click="search(offset - 20)"
          >
            上一页</button
          ><button
            type="button"
            :disabled="loading || !more"
            @click="search(offset + 20)"
          >
            下一页
          </button>
        </div>
      </div>
    </ElPopover>
  </span>
</template>
<style>
.reference-lookup,
.reference-lookup-input {
  display: block;
  width: 100%;
  min-width: 0;
}
.reference-lookup-input {
  position: relative;
}
.reference-lookup-input input {
  width: 100%;
  padding-right: 30px !important;
}
.reference-lookup-input > button {
  position: absolute;
  right: 3px;
  top: 3px;
  bottom: 3px;
  width: 28px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--ui-muted);
}
.reference-lookup-popover {
  max-width: calc(100vw - 24px);
  color: var(--ui-text);
}
.reference-lookup-heading,
.reference-lookup-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}
.reference-lookup-heading span,
.reference-lookup-footer span,
.reference-lookup-results small {
  color: var(--ui-muted);
  font-size: 12px;
}
.reference-lookup-results {
  max-height: min(320px, 45dvh);
  overflow-y: auto;
}
.reference-lookup-results > button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
  padding: 10px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--ui-border);
  background: white;
  color: var(--ui-text);
  font: inherit;
}
.reference-lookup-results > button:hover {
  background: var(--ui-soft);
}
.reference-lookup-results > button[aria-selected="true"] {
  background: var(--ui-tint);
  box-shadow: inset 3px 0 var(--ui-accent);
}
.reference-lookup-results > button[aria-selected="true"] b {
  color: var(--ui-accent);
}
.reference-lookup-results > button:disabled {
  opacity: 0.6;
  cursor: default;
}
.reference-lookup-results b {
  font-weight: 550;
  display: block;
  font-size: 14px;
}
.reference-lookup-results small {
  display: block;
  margin-top: 4px;
}
.reference-lookup-footer {
  border-top: 1px solid var(--ui-border);
}
.reference-lookup-footer span {
  margin-right: auto;
}
.reference-lookup-footer button {
  border: 1px solid var(--ui-border);
  background: white;
  color: var(--ui-accent);
  padding: 6px 10px;
  border-radius: 4px;
}
.reference-lookup-footer button:disabled {
  opacity: 0.5;
}
</style>

<style>
.reference-lookup-heading {
  flex-wrap: wrap;
  gap: 4px 12px;
}
.reference-lookup-results > button > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.reference-lookup-results > button > small {
  flex-shrink: 0;
}
</style>
