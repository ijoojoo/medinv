<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, toRef } from "vue";
import { columnLabel, entryColumnWidths } from "./documentEntry";
import {
  documentCell,
  reloadDocumentColumns,
  useDocumentColumns,
  type DocumentMode,
  type DocumentStage,
  type DocumentValues,
} from "./documentColumns";
import { downloadEntryCsv, entryCsv } from "./entryFiles";
import DocumentColumnSettings from "./DocumentColumnSettings.vue";
const props = withDefaults(
  defineProps<{
    entity: string;
    stage: DocumentStage;
    mode?: DocumentMode;
    rows: DocumentValues[];
    title?: string;
    disabled?: boolean;
    actions?: boolean;
  }>(),
  { mode: "view", title: "单据明细" },
);
const state = useDocumentColumns(
  toRef(props, "entity"),
  toRef(props, "stage"),
  toRef(props, "mode"),
);
const configuring = ref(false),
  viewport = ref<HTMLElement>(),
  available = ref(0);
const shown = computed(() => state.value.columns.filter((c) => c.visible));
const widths = computed(() => entryColumnWidths(shown.value, available.value));
const tableWidth = computed(() =>
  widths.value.reduce((s, n) => s + n, props.actions ? 124 : 44),
);
let observer: ResizeObserver | undefined;
onMounted(() => {
  observer = new ResizeObserver(
    (e) => (available.value = e[0]?.contentRect.width || 0),
  );
  if (viewport.value) observer.observe(viewport.value);
});
onUnmounted(() => observer?.disconnect());
function exportView() {
  downloadEntryCsv(
    entryCsv(
      ["行", ...shown.value.map(columnLabel)],
      props.rows.map((r, i) => [
        String(i + 1),
        ...shown.value.map((c) => documentCell(r, c)),
      ]),
    ),
    `${props.title}-当前视图.csv`,
  );
}
function keydown(event: KeyboardEvent) {
  if (
    props.mode !== "editor" ||
    event.defaultPrevented ||
    event.isComposing ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    !["Enter", "Tab"].includes(event.key)
  )
    return;
  const input = event.target as HTMLElement;
  if (
    !input.closest<HTMLElement>("[data-grid-cell]") ||
    input.tagName === "TEXTAREA" ||
    input.closest('[role="listbox"]')
  )
    return;
  if (advanceCell(input, event.shiftKey)) event.preventDefault();
  else if (event.key === "Enter") event.preventDefault();
}
function advanceEvent(event: Event) {
  advanceCell(
    event.target as HTMLElement,
    (event as CustomEvent<{ reverse: boolean }>).detail.reverse,
  );
}
function advanceCell(input: HTMLElement, reverse: boolean) {
  const cell = input.closest<HTMLElement>("[data-grid-cell]");
  if (!cell) return false;
  const cells = [
    ...viewport.value!.querySelectorAll<HTMLElement>(
      '[data-grid-cell][data-stop="true"]',
    ),
  ].filter((c) =>
    c.querySelector(
      "input:not([disabled]):not([readonly]),select:not([disabled])",
    ),
  );
  const index = cells.indexOf(cell);
  const before = cells.filter(
    (c) => c.compareDocumentPosition(cell) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).length;
  const next =
    cells[index < 0 ? before + (reverse ? -1 : 0) : index + (reverse ? -1 : 1)];
  if (next) {
    next
      .querySelector<HTMLElement>(
        "input:not([disabled]),select:not([disabled]),button:not([disabled])",
      )
      ?.focus({ preventScroll: true });
    const bounds = viewport.value!.getBoundingClientRect(),
      rect = next.getBoundingClientRect();
    if (rect.top < bounds.top + 40)
      viewport.value!.scrollTop -= bounds.top + 40 - rect.top;
    else if (rect.bottom > bounds.bottom - 12)
      viewport.value!.scrollTop += rect.bottom - bounds.bottom + 12;
    if (rect.left < bounds.left)
      viewport.value!.scrollLeft -= bounds.left - rect.left;
    else if (rect.right > bounds.right)
      viewport.value!.scrollLeft += rect.right - bounds.right;
    return true;
  }
  const section = viewport.value!.closest(".document-line-grid")!,
    form = input.closest("form");
  const outside = [
    ...(form?.querySelectorAll<HTMLElement>("input,select,textarea,button") ||
      []),
  ].filter(
    (el) =>
      !section.contains(el) &&
      el.tabIndex >= 0 &&
      !el.matches(":disabled") &&
      el.getClientRects().length,
  );
  const destination = reverse
    ? outside
        .filter(
          (el) =>
            el.compareDocumentPosition(section) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        )
        .at(-1)
    : outside.find(
        (el) =>
          section.compareDocumentPosition(el) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      );
  if (destination) {
    destination.focus();
    return true;
  }
  return false;
}
function selectNumber(event: FocusEvent) {
  const input = event.target;
  if (
    props.mode === "editor" &&
    input instanceof HTMLInputElement &&
    input.inputMode === "decimal"
  )
    input.select();
}
function pointerNumber(event: PointerEvent) {
  const input = event.target;
  if (
    input instanceof HTMLInputElement &&
    input.inputMode === "decimal" &&
    document.activeElement !== input
  )
    input.addEventListener(
      "mouseup",
      (e) => {
        e.preventDefault();
        input.select();
      },
      { once: true },
    );
}
</script>
<template>
  <section class="document-line-grid" :aria-label="title">
    <div class="document-grid-toolbar">
      <b
        >{{ title }} <small>{{ rows.length }} 行</small></b
      ><span />
      <el-button
        size="small"
        :disabled="!state.ready || !rows.length"
        @click="exportView"
        >导出当前视图</el-button
      >
      <el-button
        size="small"
        :disabled="!state.ready || disabled"
        @click="configuring = true"
        >配置单据明细列</el-button
      >
    </div>
    <p v-if="state.error" role="alert">
      {{ state.error }}
      <el-button link @click="reloadDocumentColumns(state)">重新读取</el-button>
    </p>
    <div
      ref="viewport"
      class="document-grid-scroll"
      @keydown="keydown"
      @focusin="selectNumber"
      @pointerdown="pointerNumber"
      @document-grid-advance="advanceEvent"
      :aria-busy="state.loading"
    >
      <table
        :aria-label="title + '表'"
        :style="{ width: tableWidth + 'px', minWidth: '100%' }"
      >
        <colgroup>
          <col style="width: 44px" />
          <col
            v-for="(c, i) in shown"
            :key="c.key"
            :style="{ width: widths[i] + 'px' }"
          />
          <col v-if="actions" style="width: 80px" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">行</th>
            <th v-for="c in shown" :key="c.key" scope="col">
              {{ columnLabel(c) }}
            </th>
            <th v-if="actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in rows" :key="row.row_key || index">
            <th scope="row">{{ index + 1 }}</th>
            <td
              v-for="c in shown"
              :key="c.key"
              :data-grid-cell="c.key"
              :data-stop="c.editable && c.tab_stop ? 'true' : 'false'"
              :class="{
                'document-cell-wrap': c.wrap,
                'document-cell-number':
                  state.fields.find((f) => f.key === c.key)?.type === 'number',
              }"
            >
              <slot name="cell" :row="row" :index="index" :column="c"
                ><span
                  :class="{
                    'document-cell-missing': row.document_status?.[c.key],
                  }"
                  :title="documentCell(row, c)"
                  >{{ documentCell(row, c) }}</span
                ></slot
              >
            </td>
            <td v-if="actions">
              <slot name="actions" :row="row" :index="index" />
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="form-note">暂无明细</p>
    </div>
    <div class="document-grid-footnote">
      {{
        mode === "view"
          ? "商品信息按单据留存值显示；历史未留存、未维护和不适用分别标注。当前视图导出不替代正式完整单据。"
          : "Enter / Tab 按停留列换格；↑↓ 选择下拉值。列配置只改变显示。"
      }}
    </div>
    <DocumentColumnSettings
      v-if="configuring"
      :state="state"
      @close="configuring = false"
    />
  </section>
</template>
<style>
.document-line-grid {
  min-width: 0;
  max-width: 100%;
  border: 1px solid var(--el-border-color);
  border-radius: 7px;
  overflow: hidden;
  margin: 12px 0;
  background: var(--el-bg-color);
}
.document-grid-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: var(--el-fill-color-light);
  flex-wrap: wrap;
}
.document-grid-toolbar > span {
  flex: 1;
}
.document-grid-toolbar small {
  font-weight: 400;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: 8px;
}
.document-grid-toolbar .el-button + .el-button {
  margin-left: 0;
}
.document-grid-scroll {
  overflow: auto;
  max-height: 440px;
  overscroll-behavior: contain;
  scroll-padding-top: 40px;
  scroll-padding-bottom: 12px;
}
.document-grid-scroll table {
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
  font-size: 13px;
}
.document-grid-scroll th,
.document-grid-scroll td {
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid var(--el-border-color-lighter);
  box-sizing: border-box;
  vertical-align: middle;
}
.document-grid-scroll thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--el-fill-color-light);
  font-size: 12px;
  font-weight: 600;
  height: 36px;
}
.document-grid-scroll tbody th {
  font-weight: 400;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.document-grid-scroll tbody tr:focus-within,
.document-grid-scroll tbody tr:hover {
  background: var(--el-color-primary-light-9);
}
.document-grid-scroll td > span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.document-grid-scroll td.document-cell-wrap > span {
  white-space: normal;
  overflow-wrap: anywhere;
}
.document-grid-scroll td.document-cell-number {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.document-cell-missing {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.document-grid-footnote {
  padding: 7px 12px;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  background: var(--el-fill-color-lighter);
}
.document-grid-scroll input,
.document-grid-scroll select {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 32px;
  padding: 4px 7px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
}
</style>
