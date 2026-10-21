<script setup lang="ts">
import PartyLookup from "./PartyLookup.vue";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { session } from "./api";
import { loadOptions, type PurchaseOptions } from "./business";
import type { SalesOptions } from "./sales";
import { submitCommand, unresolved } from "./commandRecovery";
import DocumentEntryGrid from "./DocumentEntryGrid.vue";
import SaleStockPanel from "./SaleStockPanel.vue";
import {
  entryResumeKey,
  readEntryResume,
  writeEntryResume,
  type EntryResume,
} from "./entryResume";
import {
  editors,
  contentWire,
  isDirty,
  editorChanged,
  saveDraft,
  convertedEditor,
  createNextEditor,
  goPage,
  moduleFor,
  reloadDraft,
  copyDraft,
  showRecord,
} from "./workspace";
const props = defineProps<{ id: string; active: boolean }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const state = computed(() => editors[props.id]!);
const root = ref<HTMLElement>();
const carryHeader = ref(state.value.continuation?.carry_header ?? true);
const purchase = computed(() => state.value.record.channel === "purchase");
const carriedFields = computed(() =>
  purchase.value
    ? "供应商、收货仓库"
    : "客户、发货仓库、收货地址、最低剩余效期及账期",
);
const revision = computed(() => !!state.value.record.source_order_id);
const title = computed(
  () =>
    `${revision.value ? "修改" : "新建"}${purchase.value ? "采购" : "销售"}单`,
);
const entityName = computed(
  () =>
    session.value?.entities.find((e) => e.id === state.value.record.entity_id)
      ?.name || "",
);
const entry = ref<InstanceType<typeof DocumentEntryGrid>>();
const partyInput = ref<InstanceType<typeof PartyLookup>>();
const choices = ref<PurchaseOptions & SalesOptions>({
  suppliers: [],
  customers: [],
  warehouses: [],
  skus: [],
  locations: [],
});
const loading = ref(true),
  optionsError = ref(""),
  maintaining = ref(false);
const blocked = computed(
  () =>
    maintaining.value ||
    state.value.submitting ||
    !!unresolved.value ||
    state.value.record.state !== "EDITING",
);
const positionKey = entryResumeKey(
  session.value!.tenant_id,
  session.value!.principal_id,
  state.value.record.entity_id,
  props.id,
);
let position = readEntryResume(positionKey);
let resumePending = props.active;
let activationFocus = document.activeElement;
function rememberPosition(event: FocusEvent) {
  if (!props.active || blocked.value) return;
  const target = event.target;
  if (!(
    target instanceof HTMLInputElement || target instanceof HTMLSelectElement
  ))
    return;
  if (
    target instanceof HTMLInputElement &&
    target.type === "number" &&
    target.closest(".editor-header-fields")
  )
    target.select();
  const row = target.closest<HTMLElement>("[data-row-key]");
  const column = target.closest<HTMLElement>("[data-col]")?.dataset.col;
  const field =
    target.closest<HTMLElement>("[data-entry-field]")?.dataset.entryField;
  let next: EntryResume | undefined;
  if (row && column) {
    const key = row.dataset.rowKey!;
    const index = state.value.content.lines.findIndex(
      (r) => r.client_key === key,
    );
    if (index >= 0) next = { area: "line", key, index, column };
  } else if (field) next = { area: "header", field };
  if (next) {
    position = next;
    writeEntryResume(positionKey, next);
  }
}
watch(
  () => props.active,
  (active) => {
    resumePending = active;
    activationFocus = document.activeElement;
  },
  { flush: "sync" },
);
watch(
  () =>
    [
      props.active,
      loading.value,
      entry.value?.ready,
      partyInput.value?.ready,
    ] as const,
  async ([active, busy, ready, partyReady]) => {
    if (!active || busy || !ready || !partyReady || !resumePending) return;
    resumePending = false;
    await nextTick();
    const focused = document.activeElement;
    if (
      !alive ||
      !props.active ||
      blocked.value ||
      optionsError.value ||
      focused?.closest('[role="dialog"],.el-overlay') ||
      root.value?.contains(focused) ||
      (focused !== document.body && focused !== activationFocus)
    )
      return;
    if (position?.area === "line") {
      await entry.value?.resumePosition(position);
    } else if (position?.area === "header") {
      root.value
        ?.querySelector<HTMLElement>(
          `[data-entry-field="${position.field}"] input:not(:disabled),[data-entry-field="${position.field}"] select:not(:disabled)`,
        )
        ?.focus({ preventScroll: true });
    } else if (state.value.continuation) {
      const missing = root.value?.querySelector<HTMLElement>(
        ".editor-header-fields :invalid",
      );
      if (missing) missing.focus();
      else entry.value?.focusFirstProduct();
    }
  },
  { flush: "post" },
);
let alive = true;
onUnmounted(() => {
  alive = false;
});
async function loadChoices() {
  loading.value = true;
  optionsError.value = "";
  try {
    const result = await loadOptions<PurchaseOptions & SalesOptions>(
      `/api/v1/${purchase.value ? "purchasing" : "selling"}/options?entity_id=${state.value.record.entity_id}&include_skus=false&include_parties=false`,
    );
    if (alive) choices.value = result;
  } catch (e) {
    if (alive)
      optionsError.value = e instanceof Error ? e.message : "资料读取失败";
  } finally {
    if (alive) loading.value = false;
  }
}
onMounted(() => {
  void loadChoices();
  if (isDirty(state.value)) editorChanged(props.id);
});
watch(
  () => JSON.stringify(contentWire(state.value.content)),
  () => editorChanged(props.id),
);
async function storeDraft() {
  try {
    await saveDraft(props.id);
    if (alive) ElMessage.success("编辑草稿已保存");
  } catch (e) {
    emit("failure", e);
  }
}
async function maintain(page: "products" | "parties") {
  if (blocked.value || state.value.conflict) return;
  maintaining.value = true;
  try {
    await saveDraft(props.id);
    if (!alive || !props.active) return;
    goPage(page);
    ElMessage.info("草稿已保存；维护完成后，点击原单据页签继续录入。");
  } catch (error) {
    emit("failure", error);
  } finally {
    maintaining.value = false;
  }
}
async function resolveConflict(copy = false) {
  try {
    if (copy) await copyDraft(props.id);
    else await reloadDraft(props.id);
  } catch (e) {
    if (e !== "cancel" && e !== "close") emit("failure", e);
  }
}
async function save(event: SubmitEvent) {
  if (
    blocked.value ||
    loading.value ||
    entry.value?.hydrating ||
    optionsError.value ||
    state.value.conflict
  )
    return;
  const rows = entry.value?.prepare();
  if (!rows) return;
  const current = state.value;
  const next =
    !revision.value && event.submitter?.hasAttribute("data-save-next");
  const carry = carryHeader.value;
  current.submitting = true;
  try {
    await saveDraft(props.id);
    if (!alive) return;
    const c = current.content,
      r = current.record;
    const body = {
      ...(revision.value
        ? { expected_version: r.source_version, reason: c.reason }
        : { entity_id: r.entity_id }),
      ...(purchase.value
        ? { supplier_id: c.supplier_id }
        : {
            customer_id: c.customer_id,
            delivery_address: c.delivery_address,
            minimum_shelf_days: Number(c.minimum_shelf_days),
            credit_days: Number(c.credit_days),
          }),
      warehouse_id: c.warehouse_id,
      lines: rows.map((i) => ({
        sku_id: i.sku_id,
        packaging_id: i.packaging_id || undefined,
        qty: i.qty,
        unit_price: i.unit_price,
        tax_rate: i.tax_rate,
      })),
      editor_draft: { id: r.id, version: r.version },
    };
    const base = `/api/v1/${purchase.value ? "purchase" : "sales"}-orders`;
    const result = await submitCommand<{ id: string }>(
      revision.value ? `${base}/${r.source_order_id}/revise` : base,
      body,
    );
    if (alive) {
      ElMessage.success(
        revision.value ? "变更已保存，待重新审核" : "单据已保存，待审核",
      );
      if (next) {
        try {
          await createNextEditor(props.id, result.id, carry);
        } catch (e) {
          // The source has closed; this error concerns only the next draft.
          ElMessage.warning(
            e instanceof Error
              ? e.message
              : "上一单已保存，请核对下一张草稿状态",
          );
        }
      } else convertedEditor(props.id, result.id || r.source_order_id!);
    }
  } catch (e) {
    if (alive) {
      current.error = e instanceof Error ? e.message : "保存未完成";
      emit("failure", e);
    }
  } finally {
    current.submitting = false;
  }
}
function keys(e: KeyboardEvent) {
  if (!props.active || e.defaultPrevented) return;
  if (e.isComposing || e.keyCode === 229) {
    if (e.key === "Enter") e.preventDefault();
    return;
  }
  if ((e.target as HTMLElement).closest(".el-dialog")) return;
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    if (!blocked.value) {
      const form = (e.target as HTMLElement).closest("form");
      const next = form?.querySelector<HTMLButtonElement>("[data-save-next]");
      if (e.shiftKey) {
        if (next && !next.disabled) form?.requestSubmit(next);
      } else form?.requestSubmit();
    }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (!blocked.value) void storeDraft();
    return;
  }
  const target = e.target;
  if (
    e.key !== "Enter" ||
    e.ctrlKey ||
    e.metaKey ||
    e.altKey ||
    !(
      target instanceof HTMLInputElement || target instanceof HTMLSelectElement
    ) ||
    !target.closest(".editor-header-fields")
  )
    return;
  // Plain Enter advances the header. Formal submission remains an explicit
  // button or Ctrl+Enter action, never a native implicit form submit.
  e.preventDefault();
  if (blocked.value || loading.value || optionsError.value) return;
  const fields = Array.from(
    root.value!.querySelectorAll<HTMLInputElement | HTMLSelectElement>(
      ".editor-header-fields input:not(:disabled),.editor-header-fields select:not(:disabled)",
    ),
  );
  const index = fields.indexOf(target);
  if (index < 0 || (!e.shiftKey && !target.reportValidity())) return;
  const next = fields[index + (e.shiftKey ? -1 : 1)];
  if (next) next.focus();
  else if (!e.shiftKey) entry.value?.focusFirstProduct();
}
</script>
<template>
  <section
    ref="root"
    class="document-editor"
    role="region"
    :aria-label="title"
    @keydown="keys"
    @focusin="rememberPosition"
  >
    <header class="editor-heading">
      <div>
        <h1>{{ title }}</h1>
        <span>{{ entityName }}</span
        ><el-tag size="small" type="info">{{
          revision ? "改单草稿" : "编辑草稿"
        }}</el-tag>
      </div>
      <div class="editor-heading-actions">
        <template v-if="session?.permissions.includes('catalog.read')">
          <el-button
            :disabled="blocked || state.conflict"
            @click="maintain('parties')"
            >维护往来单位</el-button
          >
          <el-button
            :disabled="blocked || state.conflict"
            @click="maintain('products')"
            >维护商品资料</el-button
          >
        </template>
        <el-button
          :disabled="maintaining"
          @click="goPage(moduleFor(state.record.channel))"
          >返回{{ purchase ? "采购" : "销售" }}记录</el-button
        >
      </div>
    </header>
    <div v-if="state.continuation" class="editor-continuation" role="status">
      <span>{{
        state.continuation.carry_header
          ? `已沿用上一单的${carriedFields}，明细与备注已清空。`
          : "新单未沿用上一单表头，请选择往来单位和仓库。"
      }}</span>
      <el-button
        link
        @click="
          showRecord(
            state.record.channel,
            state.continuation.order_id,
            state.record.entity_id,
          )
        "
        >查看上一单</el-button
      >
    </div>
    <div
      v-if="state.record.state !== 'EDITING'"
      class="editor-notice"
      role="status"
    >
      {{
        state.record.state === "CONVERTED"
          ? "这份草稿已保存为正式单据。"
          : "这份草稿已放弃。"
      }}
      <el-button
        v-if="state.record.converted_order_id"
        @click="
          showRecord(
            state.record.channel,
            state.record.converted_order_id,
            state.record.entity_id,
          )
        "
        >查看原单</el-button
      >
    </div>
    <div
      v-if="state.error || optionsError"
      class="editor-notice warning-text"
      role="alert"
    >
      {{ state.error || optionsError }}
      <template v-if="state.conflict"
        ><el-button @click="resolveConflict()">读取已存版本</el-button
        ><el-button @click="resolveConflict(true)"
          >另存草稿</el-button
        ></template
      >
      <el-button v-if="optionsError" @click="loadChoices"
        >重试读取资料</el-button
      >
    </div>
    <form class="business-form editor-form" @submit.prevent="save">
      <fieldset class="document-entry-fields" :disabled="blocked">
        <div class="editor-header-fields">
          <label v-if="purchase" data-entry-field="party"
            >供应商<PartyLookup
              ref="partyInput"
              v-model="state.content.supplier_id"
              :entity="state.record.entity_id"
              channel="purchase"
              label="供应商"
              required
              :disabled="blocked || loading || !!optionsError"
          /></label>
          <label v-else data-entry-field="party"
            >客户<PartyLookup
              ref="partyInput"
              v-model="state.content.customer_id"
              :entity="state.record.entity_id"
              channel="sale"
              label="客户"
              required
              :disabled="blocked || loading || !!optionsError"
          /></label>
          <label data-entry-field="warehouse"
            >{{ purchase ? "收货仓库" : "发货仓库"
            }}<select
              v-model="state.content.warehouse_id"
              required
              :disabled="loading || !!optionsError"
            >
              <option disabled value="">请选择仓库</option>
              <option v-for="w in choices.warehouses" :key="w.id" :value="w.id">
                {{ w.name }}
              </option>
            </select></label
          >
          <label data-entry-field="title"
            >草稿备注<input
              v-model="state.record.title"
              maxlength="120"
              placeholder="便于在我的草稿中查找"
              @input="editorChanged(id)"
          /></label>
          <template v-if="!purchase">
            <label class="editor-address" data-entry-field="address"
              >收货地址<input
                v-model="state.content.delivery_address"
                required
                maxlength="500"
            /></label>
            <label data-entry-field="shelf"
              >最低剩余效期（天）<input
                v-model="state.content.minimum_shelf_days"
                type="number"
                min="0"
                max="3650"
                step="1"
                required
            /></label>
            <label data-entry-field="credit"
              >账期（天）<input
                v-model="state.content.credit_days"
                type="number"
                min="0"
                max="365"
                step="1"
                required
            /></label>
          </template>
          <label v-if="revision" class="editor-reason" data-entry-field="reason"
            >变更原因<input
              v-model="state.content.reason"
              required
              maxlength="500"
              placeholder="保存后回到待审核状态"
          /></label>
        </div>
        <DocumentEntryGrid
          ref="entry"
          v-model="state.content.lines"
          :entity="state.record.entity_id"
          :channel="state.record.channel"
          :disabled="blocked"
          :active="active"
          @failure="emit('failure', $event)"
        />
        <details v-if="!purchase" class="editor-stock">
          <summary>查看库存与缺货</summary>
          <SaleStockPanel
            channel="wholesale"
            :entity="state.record.entity_id"
            :warehouse="state.content.warehouse_id"
            :skus="state.content.lines.map((r) => r.sku_id)"
            :minimum-shelf-days="Number(state.content.minimum_shelf_days)"
            :disabled="blocked"
          />
        </details>
      </fieldset>
      <footer class="editor-actions">
        <span role="status"
          >{{
            state.submitting
              ? "正在保存单据…"
              : state.saving
                ? "正在保存草稿…"
                : entry?.hydrating
                  ? "正在读取商品资料…"
                  : isDirty(state)
                    ? "有未保存修改"
                    : "草稿已保存"
          }}<small
            >表头 Enter 下一项 · Shift + Enter 上一项 · Ctrl + S 草稿 · Ctrl +
            Enter 保存<span v-if="!revision">
              · Ctrl + Shift + Enter 保存并新建</span
            ></small
          ></span
        >
        <el-button
          :disabled="blocked || state.conflict"
          :loading="state.saving"
          @click="storeDraft"
          >保存草稿</el-button
        >
        <el-button
          native-type="submit"
          type="primary"
          :disabled="
            blocked ||
            loading ||
            entry?.hydrating ||
            !!optionsError ||
            state.conflict
          "
          :loading="state.submitting"
          >{{
            revision ? "保存变更并待审" : purchase ? "保存采购单" : "保存销售单"
          }}</el-button
        >
        <div v-if="!revision" class="editor-next">
          <label
            :title="`保存并新建时沿用${carriedFields}；商品明细和备注清空`"
          >
            <input
              v-model="carryHeader"
              type="checkbox"
              :disabled="blocked"
            />沿用表头
          </label>
          <el-button
            native-type="submit"
            data-save-next
            :disabled="
              blocked ||
              loading ||
              entry?.hydrating ||
              !!optionsError ||
              state.conflict
            "
            >保存并新建</el-button
          >
        </div>
      </footer>
    </form>
  </section>
</template>
<style scoped>
.document-editor {
  background: #fff;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  min-height: calc(100dvh - 112px);
  display: flex;
  flex-direction: column;
}
.editor-heading {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  border-bottom: 1px solid var(--ui-border);
  gap: 12px;
  flex-wrap: wrap;
}
.editor-heading > div {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.editor-heading > .editor-heading-actions {
  gap: 6px;
}
.editor-heading-actions .el-button + .el-button {
  margin-left: 0;
}
.editor-heading h1 {
  font-size: 16px;
  margin: 0;
}
.editor-heading span {
  font-size: 12px;
  color: var(--ui-muted);
}
.editor-form {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  gap: 0;
}
.editor-form fieldset {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 10px;
  padding: 10px 12px;
}
.editor-header-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
}
.editor-header-fields label {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
  font-size: 13px;
}
.editor-header-fields input,
.editor-header-fields select {
  flex: 1;
  width: 0;
  min-height: 32px;
  padding: 5px 8px;
  border-color: var(--ui-border);
}
.editor-reason {
  grid-column: 1/-1;
}
.editor-form :deep(.document-entry) {
  flex: 1;
  margin: 0;
}
.editor-form :deep(.entry-scroll) {
  max-height: calc(100dvh - 380px);
  min-height: 240px;
}
.editor-actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  position: sticky;
  bottom: 0;
  background: #fff;
  padding: 8px 14px;
  border-top: 1px solid var(--ui-border);
  z-index: 5;
}
.editor-actions > span {
  margin-right: auto;
  color: var(--ui-muted);
  font-size: 12px;
}
.editor-actions small {
  display: block;
  color: var(--ui-muted);
  margin-top: 4px;
}
.editor-stock {
  font-size: 13px;
}
.editor-next {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.editor-next label {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  font-size: 12px;
  color: var(--ui-muted);
}
.editor-next input {
  width: 14px;
  height: 14px;
  min-height: 0;
  accent-color: var(--ui-accent);
}
.editor-continuation {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  flex: none;
  padding: 6px 14px;
  font-size: 12px;
  color: var(--ui-muted);
  background: var(--ui-soft);
}
.editor-stock summary {
  cursor: pointer;
  color: var(--ui-accent);
}
.editor-notice {
  flex: none;
  padding: 12px 18px;
  background: #fff9eb;
  font-size: 13px;
}
/* The grid uses the remaining desktop space; totals and actions occupy real rows. */
@media (min-width: 761px) and (min-height: 680px) {
  .document-editor {
    height: calc(var(--ui-workspace-height, calc(100dvh - 98px)) - 32px);
    min-height: 0;
  }
  .editor-form fieldset {
    min-height: 0;
    overflow: auto;
  }
  .editor-form :deep(.document-entry) {
    display: flex;
    flex-direction: column;
    flex: 1 0 280px;
    min-height: 280px;
  }
  .editor-form :deep(.document-entry > :not(.entry-scroll)) {
    flex: none;
  }
  .editor-form :deep(.entry-scroll) {
    flex: 1;
    min-height: 90px;
    max-height: none;
  }
  .editor-actions {
    position: static;
  }
}
@media (max-width: 760px) {
  .editor-header-fields {
    grid-template-columns: 1fr;
  }
  .editor-heading {
    align-items: flex-start;
  }
  .editor-heading > div {
    gap: 6px;
  }
  .editor-actions {
    flex-wrap: wrap;
  }
  .editor-actions > span {
    width: 100%;
  }
  .document-editor {
    min-height: calc(100dvh - 130px);
  }
}
</style>
