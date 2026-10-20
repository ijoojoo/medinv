<script setup lang="ts">
import {
  computed,
  nextTick,
  onUnmounted,
  ref,
  useId,
  watch,
  type CSSProperties,
} from "vue";
import { request, session } from "./api";
import EntrySearchText from "./EntrySearchText.vue";
import type { EntryChannel, EntryProduct } from "./documentEntry";
const props = defineProps<{
  modelValue: string;
  product?: EntryProduct;
  draftText?: string;
  entity: string;
  channel: EntryChannel;
  disabled?: boolean;
  hydrate?: (sku: string) => Promise<EntryProduct | undefined>;
}>();
const emit = defineEmits<{
  select: [product: EntryProduct];
  pending: [value: boolean];
  loaded: [product: EntryProduct];
  draftText: [text: string];
}>();
const input = ref<HTMLInputElement>();
const results = ref<HTMLElement>();
const composing = ref(false);
const text = ref(props.draftText || ""),
  open = ref(false),
  dirty = ref(!!props.draftText),
  loading = ref(false),
  error = ref("");
const selected = ref<EntryProduct>();
const items = ref<EntryProduct[]>([]),
  active = ref(0),
  offset = ref(0),
  more = ref(false);
const id = useId();
const position = ref<CSSProperties>({
  left: "0px",
  top: "0px",
  width: "940px",
  maxHeight: "420px",
});
let controller: AbortController | undefined;
let hydrationController: AbortController | undefined;
let pendingSearch: Promise<boolean> | undefined;
let epoch = 0,
  hydration = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
const path = computed(
  () =>
    `/api/v1/catalog/entry-products?${new URLSearchParams({ entity_id: props.entity, channel: props.channel })}`,
);
const label = (p?: EntryProduct) => (p ? `${p.name} · ${p.code}` : "");
const query = computed(() =>
  dirty.value ? text.value.trim() : selected.value?.code || "",
);
const highlighted = computed(() => items.value[active.value]);
function barcodeDetail(p: EntryProduct) {
  const match = p.barcode_match;
  if (!match) return "";
  if (!match.available)
    return `条码 ${match.barcode} 对应包装已不可用，请核对条码与包装资料`;
  if (!p.eligible)
    return `条码 ${match.barcode} 对应商品当前不可选，请核对资质与经营状态`;
  return match.packaging_id
    ? `包装条码 ${match.barcode} · 按 ${match.unit_name} 录入，${match.denominator} ${match.unit_name} ＝ ${match.numerator} ${p.base_unit}`
    : `基本单位条码 ${match.barcode} · 按 ${p.base_unit} 录入`;
}
const detail = computed(() => {
  const p = highlighted.value;
  return p
    ? [
        p.code,
        p.name,
        p.specification,
        p.manufacturer_name,
        `基本单位：${p.base_unit}`,
        barcodeDetail(p),
      ]
        .filter(Boolean)
        .join(" · ")
    : "";
});
function invalidateSearch() {
  ++epoch;
  clearTimeout(timer);
  timer = undefined;
  controller?.abort();
  pendingSearch = undefined;
  loading.value = false;
  items.value = [];
  more.value = false;
}
function close() {
  open.value = false;
  invalidateSearch();
}
function place() {
  if (!open.value || !input.value) return;
  const r = input.value.getBoundingClientRect(),
    width = Math.min(940, window.innerWidth - 24);
  const below = window.innerHeight - r.bottom - 12,
    above = r.top - 12;
  const down = below >= 260 || below >= above;
  const height = Math.max(0, Math.min(420, down ? below : above));
  position.value = {
    left: `${Math.max(8, Math.min(r.left, window.innerWidth - width - 12))}px`,
    top: down ? `${r.bottom + 4}px` : "auto",
    bottom: down ? "auto" : `${window.innerHeight - r.top + 4}px`,
    width: `${width}px`,
    maxHeight: `${height}px`,
  };
}
async function revealActive() {
  await nextTick();
  const row = document.getElementById(`${id}-${active.value}`);
  const viewport = results.value;
  if (!row || !viewport) return;
  const bounds = viewport.getBoundingClientRect();
  const heading =
    viewport.querySelector(".picker-columns")?.getBoundingClientRect().height ||
    0;
  const rect = row.getBoundingClientRect();
  const top = bounds.top + heading,
    bottom = bounds.top + viewport.clientHeight;
  if (rect.top < top) viewport.scrollTop += rect.top - top;
  else if (rect.bottom > bottom) viewport.scrollTop += rect.bottom - bottom;
}
function search(reset = true, last = false): Promise<boolean> {
  invalidateSearch();
  if (reset) offset.value = 0;
  const current = epoch;
  controller = new AbortController();
  const signal = controller.signal;
  loading.value = true;
  error.value = "";
  pendingSearch = (async () => {
    try {
      const result = await request<{
        items: EntryProduct[];
        has_more: boolean;
      }>(
        `${path.value}&search=${encodeURIComponent(query.value)}&offset=${offset.value}${
          !dirty.value && props.modelValue && !selected.value
            ? "&sku_id=" + encodeURIComponent(props.modelValue)
            : ""
        }`,
        { signal },
      );
      if (current !== epoch || !open.value) return false;
      items.value = result.items;
      more.value = result.has_more;
      active.value = last ? Math.max(0, result.items.length - 1) : 0;
      if (results.value) results.value.scrollTop = 0;
      void revealActive();
      return true;
    } catch {
      if (current === epoch) error.value = "商品查询失败，请按 Enter 重试";
      return false;
    } finally {
      if (current === epoch) loading.value = false;
    }
  })();
  return pendingSearch;
}
async function changePage(direction: number) {
  if (
    loading.value ||
    props.disabled ||
    (direction < 0 ? !offset.value : !more.value || offset.value >= 100000)
  )
    return;
  offset.value += direction * 20;
  await search(false, direction < 0);
}
function focus() {
  if (props.disabled) return;
  open.value = true;
  place();
  input.value?.select();
  void search();
}
function edit(event?: Event) {
  if (
    props.disabled ||
    composing.value ||
    (event as InputEvent | undefined)?.isComposing
  )
    return;
  invalidateSearch();
  dirty.value = true;
  emit("draftText", text.value);
  emit("pending", true);
  open.value = document.activeElement === input.value;
  place();
  input.value?.setCustomValidity("请从候选商品中选择，或按 Esc 恢复原商品");
  offset.value = 0;
  if (!open.value) return;
  loading.value = true;
  timer = setTimeout(() => {
    timer = undefined;
    void search();
  }, 180);
}
function startComposition() {
  composing.value = true;
  close();
}
async function endComposition() {
  composing.value = false;
  await nextTick();
  text.value = input.value?.value || "";
  edit();
}
function choose(p: EntryProduct) {
  if (props.disabled || !p.eligible) return;
  selected.value = p;
  text.value = label(p);
  dirty.value = false;
  close();
  input.value?.setCustomValidity("");
  emit("pending", false);
  emit("draftText", "");
  emit("select", p);
}
async function keys(event: KeyboardEvent) {
  if (
    event.isComposing ||
    composing.value ||
    props.disabled ||
    event.keyCode === 229 ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    (event.shiftKey && !dirty.value)
  )
    return;
  if (event.key === "Escape") {
    if (open.value || dirty.value) {
      event.preventDefault();
      event.stopPropagation();
      close();
      dirty.value = false;
      text.value = label(selected.value);
      input.value?.setCustomValidity("");
      emit("pending", false);
      emit("draftText", "");
    }
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    event.stopPropagation();
    if (!open.value) {
      open.value = true;
      place();
      await search();
    } else if (items.value.length)
      active.value =
        (active.value +
          (event.key === "ArrowDown" ? 1 : -1) +
          items.value.length) %
        items.value.length;
    await revealActive();
  } else if (open.value && ["PageDown", "PageUp"].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();
    await changePage(event.key === "PageDown" ? 1 : -1);
  } else if (
    event.key === "Enter" &&
    (open.value || dirty.value || !props.modelValue)
  ) {
    event.preventDefault();
    event.stopPropagation();
    open.value = true;
    place();
    const typed = text.value,
      scope = path.value;
    const ready = timer
      ? await search()
      : loading.value && pendingSearch
        ? await pendingSearch
        : error.value || !items.value.length
          ? await search(false)
          : true;
    if (
      !ready ||
      !open.value ||
      props.disabled ||
      text.value !== typed ||
      path.value !== scope ||
      document.activeElement !== input.value
    )
      return;
    const item = items.value[active.value];
    if (item) choose(item);
  } else if (event.key === "Tab") {
    if (dirty.value) {
      event.preventDefault();
      event.stopPropagation();
      input.value?.reportValidity();
    } else close();
  }
}
watch(
  () => props.draftText,
  (value) => {
    // A parent's echo of our own query must not restore the old product when cleared.
    if ((value || "") === (dirty.value ? text.value : "")) return;
    dirty.value = !!value;
    if (value) text.value = value;
    else text.value = label(selected.value);
    emit("pending", !!value);
    input.value?.setCustomValidity(
      value ? "请从候选商品中选择，或按 Esc 恢复原商品" : "",
    );
  },
);
watch(
  () =>
    [
      props.modelValue,
      props.entity,
      props.channel,
      session.value?.tenant_id,
      session.value?.principal_id,
      session.value?.context_generation,
      session.value?.permission_version,
    ] as const,
  async () => {
    close();
    hydrationController?.abort();
    hydrationController = new AbortController();
    const current = ++hydration;
    selected.value = props.product;
    if (!props.modelValue) {
      if (!dirty.value) text.value = "";
      selected.value = undefined;
      return;
    }
    if (!selected.value || selected.value.id !== props.modelValue) {
      try {
        const product = props.hydrate
          ? await props.hydrate(props.modelValue)
          : (
              await request<{ items: EntryProduct[] }>(
                `${path.value}&sku_id=${props.modelValue}`,
                { signal: hydrationController.signal },
              )
            ).items[0];
        if (current !== hydration) return;
        selected.value = product;
        if (selected.value) emit("loaded", selected.value);
      } catch {
        if (current === hydration)
          error.value = "原商品资料读取失败，可重新检索";
      }
    }
    if (current !== hydration) return;
    if (!dirty.value)
      text.value = label(selected.value) || "原商品不可见，请重新选择";
  },
  { immediate: true },
);
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) close();
  },
);
window.addEventListener("resize", place);
window.addEventListener("scroll", place, true);
onUnmounted(() => {
  close();
  hydration++;
  hydrationController?.abort();
  window.removeEventListener("resize", place);
  window.removeEventListener("scroll", place, true);
});
</script>
<template>
  <div class="entry-picker">
    <input
      ref="input"
      v-model="text"
      role="combobox"
      aria-label="商品"
      autocomplete="off"
      maxlength="128"
      :disabled="disabled"
      :aria-expanded="open"
      :aria-controls="id"
      :aria-describedby="open && detail ? id + '-detail' : undefined"
      :aria-activedescendant="
        open && items.length ? `${id}-${active}` : undefined
      "
      aria-autocomplete="list"
      placeholder="名称 / 编码 / 条码 / 别名"
      :title="
        selected
          ? [
              selected.name,
              selected.code,
              selected.specification,
              selected.manufacturer_name,
            ]
              .filter(Boolean)
              .join(' · ')
          : undefined
      "
      @focus="focus"
      @input="edit"
      @compositionstart="startComposition"
      @compositionend="endComposition"
      @keydown="keys"
      @blur="close"
    />
    <small v-if="selected" class="entry-product-meta"
      >{{ selected.specification }} · {{ selected.manufacturer_name
      }}<span v-if="!selected.eligible"> · 当前不可选</span></small
    >
    <small v-else-if="error" class="picker-error">{{ error }}</small>
    <Teleport to="body">
      <div
        v-if="open"
        class="entry-product-popup"
        :style="position"
        @mousedown.prevent
      >
        <div class="picker-heading">
          <b>{{ highlighted?.barcode_match ? "条码识别" : "选择商品" }}</b
          ><span>名称 / 编码 / 别名；条码请完整输入或扫描，Enter 确认</span>
        </div>
        <div ref="results" class="picker-results">
          <div class="picker-columns" aria-hidden="true">
            <span>商品编码</span><span>商品名称</span><span>规格型号</span
            ><span>生产企业</span><span>单位</span>
          </div>
          <p v-if="loading" role="status">查询中…</p>
          <p v-else-if="error" role="alert">{{ error }}</p>
          <p v-else-if="!items.length">
            没有匹配的可选商品。请核对完整条码是否已登记并启用，或换用商品编码、别名检索。
          </p>
          <div
            :id="id"
            role="listbox"
            aria-label="匹配商品"
            :aria-busy="loading"
          >
            <div
              v-for="(p, i) in items"
              :id="`${id}-${i}`"
              :key="p.id"
              role="option"
              :data-sku="p.id"
              :aria-selected="i === active"
              :aria-disabled="!p.eligible"
              :class="{ active: i === active }"
              @mouseenter="active = i"
              @click="choose(p)"
            >
              <span :title="p.code"
                ><EntrySearchText :value="p.code" :query="query"
              /></span>
              <strong :title="p.name"
                ><EntrySearchText :value="p.name" :query="query"
              /></strong>
              <span :title="p.specification"
                ><EntrySearchText
                  :value="p.specification || '—'"
                  :query="query"
              /></span>
              <span :title="p.manufacturer_name"
                ><EntrySearchText
                  :value="p.manufacturer_name || '—'"
                  :query="query"
              /></span>
              <span :title="barcodeDetail(p) || p.base_unit">{{
                p.barcode_match?.unit_name || p.base_unit
              }}</span>
            </div>
          </div>
        </div>
        <div
          v-if="detail"
          :id="id + '-detail'"
          class="picker-detail"
          :class="{ 'picker-barcode-detail': highlighted?.barcode_match }"
          :title="detail"
        >
          {{ detail }}
        </div>
        <div class="picker-pager">
          <button
            type="button"
            :disabled="!offset || loading"
            tabindex="-1"
            @click="changePage(-1)"
          >
            上一批</button
          ><span class="picker-range" role="status">{{
            loading
              ? "正在查询"
              : items.length
                ? `第 ${offset + 1}–${offset + items.length} 条`
                : "暂无结果"
          }}</span
          ><span>↑↓ 选择 · PgUp/PgDn 翻页 · Enter 确认 · Esc 取消</span
          ><button
            type="button"
            :disabled="!more || loading || offset >= 100000"
            tabindex="-1"
            @click="changePage(1)"
          >
            下一批
          </button>
        </div>
      </div>
    </Teleport>
  </div>
</template>
<style>
.entry-picker small {
  display: block;
  font-size: 11px;
  color: #728078;
  white-space: normal;
  line-height: 1.4;
  margin-top: 5px;
}
.entry-product-popup {
  --picker-columns: 136px minmax(210px, 1.3fr) minmax(150px, 1fr)
    minmax(180px, 1.1fr) 56px;
  position: fixed;
  z-index: 5000;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: white;
  box-shadow: 0 12px 40px #243d342b;
  color: var(--ui-text);
  font-size: 13px;
}
.picker-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 16px;
  padding: 8px 10px;
  flex: none;
}
.picker-heading span {
  color: var(--ui-muted);
  font-size: 12px;
}
.picker-results {
  overflow: auto;
  min-height: 0;
  flex: 1 1 auto;
}
.picker-results > p {
  margin: 0;
  padding: 18px 12px;
}
.picker-columns,
.entry-product-popup [role="option"] {
  display: grid;
  grid-template-columns: var(--picker-columns);
  min-width: 732px;
  align-items: center;
}
.picker-columns {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--ui-tint);
  color: var(--ui-muted);
  border-block: 1px solid var(--ui-border);
}
.picker-columns > span {
  padding: 7px 9px;
}
.entry-product-popup [role="option"] > * {
  min-width: 0;
  padding: 9px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 400;
}
.entry-product-popup [role="option"] {
  cursor: pointer;
  min-height: 38px;
  border-bottom: 1px solid var(--ui-border);
}
.entry-product-popup [role="option"].active {
  background: var(--ui-soft);
  box-shadow: inset 3px 0 var(--ui-accent);
}
.entry-product-popup [role="option"][aria-disabled="true"] {
  opacity: 0.55;
  cursor: not-allowed;
}
.entry-product-popup mark {
  background: #fff0b3;
  color: #674200;
  border-radius: 2px;
}
.picker-detail {
  flex: none;
  padding: 7px 10px;
  border-top: 1px solid var(--ui-border);
  background: var(--ui-tint);
  color: var(--ui-muted);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.picker-pager {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px;
  padding: 7px 10px;
  border-top: 1px solid var(--ui-border);
  font-size: 12px;
  color: var(--ui-muted);
}
.picker-barcode-detail {
  white-space: normal;
  overflow-wrap: anywhere;
  color: var(--ui-accent);
}
.picker-pager button {
  border: 1px solid var(--ui-border);
  border-radius: 4px;
  background: var(--ui-soft);
  color: var(--ui-accent);
  padding: 4px 8px;
  cursor: pointer;
}
.picker-pager button:disabled {
  opacity: 0.5;
  cursor: default;
}
.picker-error {
  color: #b23b30 !important;
}
</style>
