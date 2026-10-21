<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import {
  can,
  labels,
  quantity,
  type PageData,
  type PurchaseOptions,
} from "./business";
import { createEditor, listCacheKey, recordSaved } from "./workspace";
import {
  documentQuery,
  emptyDocumentSearch,
  type DocumentSearch,
} from "./documentSearch";
import { localTime } from "./sales";
import {
  openPurchaseTask,
  purchaseWorkflowChanged,
  workflowStates,
  workflowDateLabel,
  type PurchaseWorkItem,
  type PurchaseStage,
  type PurchaseView,
} from "./purchaseWorkflow";
import DocumentFilters from "./DocumentFilters.vue";
import EditorDrafts from "./EditorDrafts.vue";
const props = defineProps<{
  entity: string;
  stage: PurchaseStage;
  view: PurchaseView;
  active: boolean;
  options: PurchaseOptions;
}>();
const emit = defineEmits<{ failure: [unknown]; toggleView: [] }>();
const filters = ref(emptyDocumentSearch()),
  offset = ref(0),
  sort = ref("newest");
const loading = ref(false),
  creating = ref(false),
  error = ref(false),
  loaded = ref(false);
const data = ref<PageData<PurchaseWorkItem>>({ items: [], has_more: false });
const selected = ref<PurchaseWorkItem | null>(null);
const table = ref<any>();
let epoch = 0,
  refreshNeeded = true,
  scrollTop = 0,
  scrollLeft = 0;
const states = computed(() => workflowStates(props.stage, props.view));
const dateLabel = computed(() => workflowDateLabel(props.stage, props.view));
const actionLabel = computed(
  () =>
    ({
      orders: "新建采购订单",
      receiving: "登记收货",
      inspection: "开始验收",
      posting: "确认入库",
    })[props.stage],
);
const permission = computed(
  () =>
    ({
      orders: "purchase.write",
      receiving: "purchase.receive",
      inspection: "purchase.inspect",
      posting: "purchase.post",
    })[props.stage],
);
const historyLabel = computed(
  () =>
    ({
      orders: "采购记录",
      receiving: "收货记录",
      inspection: "验收记录",
      posting: "入库记录",
    })[props.stage],
);
const pendingLabel = computed(
  () =>
    ({
      orders: "全部订单",
      receiving: "待收货",
      inspection: "待验收",
      posting: "待入库",
    })[props.stage],
);
const metrics = computed(() => {
  if (
    props.stage === "orders" ||
    (props.stage === "receiving" && props.view === "pending")
  )
    return [
      ["ordered", "订购"],
      ["received", "已收"],
      ["pending", "待收"],
      ["closed", "关闭"],
    ];
  if (
    props.stage === "receiving" ||
    (props.stage === "inspection" && props.view === "pending")
  )
    return [
      ["received", "实收"],
      ["pending", "待验"],
      ["qualified", "合格"],
      ["rejected", "不合格"],
      ["posted", "已入库"],
    ];
  return props.stage === "posting"
    ? [["qualified", props.view === "pending" ? "可入库" : "本次入库"]]
    : [
        ["qualified", "合格"],
        ["rejected", "不合格"],
      ];
});
function cacheKey() {
  return `${listCacheKey("purchase")}:workflow:${props.entity}:${props.stage}:${props.view}`;
}
try {
  const cached = JSON.parse(sessionStorage.getItem(cacheKey()) || "null");
  if (cached) {
    filters.value = { ...emptyDocumentSearch(), ...cached.filters };
    offset.value =
      Number.isSafeInteger(cached.offset) &&
      cached.offset >= 0 &&
      cached.offset <= 100000
        ? cached.offset
        : 0;
    sort.value = cached.sort === "oldest" ? "oldest" : "newest";
  }
} catch {
  /* Defaults work when browser storage is unavailable. */
}
function remember() {
  try {
    sessionStorage.setItem(
      cacheKey(),
      JSON.stringify({
        filters: filters.value,
        offset: offset.value,
        sort: sort.value,
      }),
    );
  } catch {
    /* Queries do not depend on local storage. */
  }
}
function scrolled(value: { scrollTop: number; scrollLeft: number }) {
  if (!props.active) return;
  scrollTop = value.scrollTop;
  scrollLeft = value.scrollLeft;
}
async function load(reset = false) {
  if (!props.entity) return;
  if (reset) {
    offset.value = 0;
    scrollTop = 0;
    scrollLeft = 0;
  }
  const current = ++epoch;
  loading.value = true;
  error.value = false;
  remember();
  const oldId = selected.value?.id;
  try {
    const params = new URLSearchParams(
      documentQuery(props.entity, offset.value, filters.value),
    );
    params.set("stage", props.stage);
    params.set("view", props.view);
    params.set("sort", sort.value);
    const rows = await request<PageData<PurchaseWorkItem>>(
      `/api/v1/purchasing/workflow?${params}`,
    );
    if (current !== epoch) return;
    data.value = rows;
    loaded.value = true;
    refreshNeeded = false;
    selected.value = rows.items.find((row) => row.id === oldId) || null;
    await nextTick();
    table.value?.setCurrentRow(selected.value);
    restoreScroll();
  } catch (e) {
    if (current === epoch) {
      error.value = true;
      emit("failure", e);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function restoreScroll() {
  table.value?.setScrollTop(scrollTop);
  table.value?.setScrollLeft(scrollLeft);
}
function apply(value: DocumentSearch) {
  filters.value = value;
  void load(true);
}
async function primary() {
  if (props.stage !== "orders") {
    if (selected.value?.action && !error.value && !loading.value)
      openPurchaseTask(selected.value, props.entity, true);
    return;
  }
  creating.value = true;
  try {
    await createEditor("purchase", props.entity, {
      warehouse_id: props.options.warehouses[0]?.id || "",
    });
  } catch (e) {
    emit("failure", e);
  } finally {
    creating.value = false;
  }
}
function refresh() {
  refreshNeeded = true;
  if (props.active) void load();
}
watch(purchaseWorkflowChanged, refresh);
watch(recordSaved, (target) => {
  if (target?.channel === "purchase") refresh();
});
watch(
  () => props.active,
  async (active) => {
    if (!active) return;
    await nextTick();
    if (refreshNeeded) void load();
    else {
      table.value?.doLayout();
      restoreScroll();
    }
  },
);
onMounted(() => {
  if (props.active) void load();
});
onUnmounted(() => epoch++);
</script>
<template>
  <div class="purchase-worklist" :data-purchase-list="`${stage}-${view}`">
    <div class="purchase-stagebar">
      <slot name="tabs" />
      <div class="purchase-stage-actions">
        <el-button :loading="loading" @click="load()"
          >刷新{{
            stage === "orders"
              ? "订单"
              : view === "history"
                ? "记录"
                : pendingLabel
          }}</el-button
        >
        <el-button
          v-if="can(permission) && view === 'pending'"
          type="primary"
          :loading="creating"
          :disabled="
            !entity ||
            loading ||
            (stage !== 'orders' && (!selected?.action || error))
          "
          :title="
            stage !== 'orders' && !selected
              ? '先在表格中选择一条待办'
              : selected?.action_note || ''
          "
          @click="primary"
          >{{ actionLabel }}</el-button
        >
        <EditorDrafts
          v-if="stage === 'orders' && can('purchase.write')"
          channel="purchase"
          :entity="entity"
        />
        <el-button :aria-pressed="false" @click="emit('toggleView')">{{
          view === "history" ? "返回" + pendingLabel : historyLabel
        }}</el-button>
      </div>
    </div>
    <DocumentFilters
      :applied="filters"
      :states="states"
      :disabled="loading"
      :entity="entity"
      channel="purchase"
      :warehouses="options.warehouses"
      :date-label="dateLabel"
      :state-label="view === 'history' ? '记录状态' : '单据状态'"
      placeholder="单号、供应商名称或编码"
      @apply="apply"
    />
    <el-alert
      v-if="error"
      title="查询失败，已保留上次结果；请刷新后再办理。"
      type="error"
      :closable="false"
    />
    <div class="purchase-table-area" v-loading="loading">
      <el-table
        ref="table"
        :data="data.items"
        height="100%"
        row-key="id"
        highlight-current-row
        :empty-text="
          loaded ? '没有符合条件的记录，可调整筛选条件' : '正在读取采购记录…'
        "
        @scroll="scrolled"
        @current-change="selected = $event"
      >
        <el-table-column
          v-if="stage !== 'orders' && view === 'pending'"
          label="选择"
          width="58"
        >
          <template #default="{ row }"
            ><input
              type="radio"
              :name="`purchase-${stage}`"
              :aria-label="'选择 ' + row.number"
              :checked="selected?.id === row.id"
              @click.stop="
                selected = row;
                table?.setCurrentRow(row);
              "
          /></template>
        </el-table-column>
        <el-table-column
          :label="
            stage === 'orders' || (stage === 'receiving' && view === 'pending')
              ? '采购订单号'
              : '收货单 / 来源订单'
          "
          min-width="210"
        >
          <template #default="{ row }">
            <button
              class="purchase-number"
              :title="row.number"
              @click.stop="openPurchaseTask(row, entity)"
            >
              {{ row.number }}
            </button>
            <button
              v-if="row.receipt_id"
              class="purchase-source"
              :title="row.order_number"
              @click.stop="
                openPurchaseTask(
                  {
                    ...row,
                    receipt_id: null,
                    inspection_id: null,
                    segment_id: null,
                  },
                  entity,
                )
              "
            >
              来源：{{ row.order_number }}
            </button>
          </template>
        </el-table-column>
        <el-table-column
          prop="supplier_name"
          label="供应商"
          min-width="160"
          show-overflow-tooltip
        />
        <el-table-column
          prop="warehouse_name"
          label="收货仓库"
          min-width="115"
          show-overflow-tooltip
        />
        <el-table-column label="数量 / 进度（按基本单位）" min-width="245">
          <template #default="{ row }"
            ><div
              v-for="group in row.quantities"
              :key="group.unit"
              class="purchase-quantity"
            >
              <b>{{ group.unit }}</b
              ><span
                v-for="[key, label] in metrics.filter(
                  ([key]) => key !== 'closed' || Number(group[key]) > 0,
                )"
                :key="key"
                >{{ label }} <strong>{{ quantity(group[key]) }}</strong></span
              >
            </div></template
          >
        </el-table-column>
        <el-table-column label="状态" width="122"
          ><template #default="{ row }"
            ><el-tag
              :type="
                ['POSTED', 'DONE', 'RECEIVED', 'RECORDED'].includes(row.state)
                  ? 'success'
                  : 'info'
              "
              >{{
                stage === "orders"
                  ? labels[row.approval_state]
                  : states[row.state] || row.state
              }}</el-tag
            >
            <small v-if="stage === 'orders'" class="cell-sub">{{
              labels[row.fulfillment_state]
            }}</small></template
          ></el-table-column
        >
        <el-table-column :label="dateLabel" width="158"
          ><template #default="{ row }">{{
            localTime(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column label="操作" width="132" fixed="right"
          ><template #default="{ row }">
            <el-button
              v-if="row.action"
              link
              type="primary"
              :disabled="loading || error"
              @click.stop="openPurchaseTask(row, entity, true)"
              >{{ actionLabel }}</el-button
            >
            <el-button link @click.stop="openPurchaseTask(row, entity)">{{
              stage === "orders" ? "查看采购单" : "查看关联记录"
            }}</el-button>
            <small v-if="row.action_note" class="cell-sub">{{
              row.action_note
            }}</small>
          </template></el-table-column
        >
      </el-table>
    </div>
    <footer class="purchase-list-footer">
      <span
        >{{
          stage === "orders"
            ? "全部订单"
            : view === "history"
              ? historyLabel
              : pendingLabel
        }}
        · 第 {{ offset / 50 + 1 }} 页 · 本页 {{ data.items.length }} 条<span
          v-if="selected"
        >
          · 已选 1 条</span
        ></span
      >
      <div>
        <label class="purchase-sort"
          >日期排序<select
            v-model="sort"
            aria-label="日期排序"
            :disabled="loading"
            @change="load(true)"
          >
            <option value="newest">最新在前</option>
            <option value="oldest">最早在前</option>
          </select></label
        >
        <el-button
          :disabled="!offset || loading"
          @click="
            offset -= 50;
            load();
          "
          >上一页</el-button
        >
        <el-button
          :disabled="!data.has_more || loading || offset >= 100000"
          @click="
            offset += 50;
            load();
          "
          >下一页</el-button
        >
      </div>
    </footer>
  </div>
</template>
<style scoped>
.purchase-worklist {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.purchase-stagebar {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--ui-border);
  min-height: 46px;
  padding: 0 12px;
  flex: none;
}
.purchase-stage-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
}
.purchase-stage-actions :deep(.el-button) {
  margin: 0;
  height: 32px;
  padding: 0 12px;
}
.purchase-worklist :deep(.document-filters) {
  flex: none;
  gap: 8px;
  padding: 10px 12px;
  margin: 0;
  border-bottom: 1px solid var(--ui-border);
}
.purchase-worklist :deep(.document-filters > label) {
  flex: 0 1 118px;
  gap: 4px !important;
  font-size: 12px;
}
.purchase-worklist :deep(.document-filters .document-keyword) {
  flex: 1 1 190px;
}
.purchase-worklist :deep(.document-filters input),
.purchase-worklist :deep(.document-filters select) {
  min-height: 32px;
  height: 32px;
  padding: 4px 8px !important;
}
.purchase-worklist :deep(.document-filter-actions) {
  min-height: 32px;
  gap: 6px;
}
.purchase-worklist :deep(.document-filter-actions .el-button) {
  margin: 0;
  height: 32px;
  padding: 0 10px;
}
.purchase-worklist :deep(.document-extra-filters) {
  padding: 10px;
  gap: 10px;
}
.purchase-table-area {
  flex: 1;
  min-height: 160px;
  padding: 0 12px;
  overflow: hidden;
}
.purchase-table-area :deep(td.el-table__cell) {
  height: 44px;
  padding: 6px 0;
}
.purchase-worklist :deep(.document-filters > label:has(.date-control)) {
  flex: 0 0 144px;
}
.purchase-number,
.purchase-source {
  display: block;
  max-width: 100%;
  border: 0;
  padding: 0;
  background: none;
  color: var(--ui-accent);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.purchase-source {
  color: var(--ui-muted);
  font-size: 12px;
  margin-top: 4px;
}
.purchase-number:hover,
.purchase-source:hover {
  text-decoration: underline;
}
.purchase-quantity {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 10px;
  font-size: 12px;
}
.purchase-quantity b {
  color: var(--ui-muted);
  font-weight: normal;
}
.purchase-quantity strong {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.purchase-table-area :deep(.el-button + .el-button) {
  margin-left: 0;
}
.purchase-list-footer {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border-top: 1px solid var(--ui-border);
  color: var(--ui-muted);
  font-size: 12px;
}
.purchase-list-footer > div {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.purchase-list-footer .el-button {
  margin: 0;
}
.purchase-sort {
  display: flex;
  align-items: center;
  gap: 6px;
}
.purchase-sort select {
  border: 1px solid var(--ui-border);
  border-radius: 4px;
  padding: 5px;
  background: white;
  color: var(--ui-text);
}
@media (max-width: 1100px) {
  .purchase-stagebar {
    flex-wrap: wrap;
    gap: 0;
  }
  .purchase-stage-actions {
    margin-left: auto;
  }
}
@media (max-width: 600px) {
  .purchase-stagebar {
    padding: 0 8px;
  }
  .purchase-stage-actions {
    justify-content: flex-start;
    margin-left: 0;
  }
  .purchase-worklist :deep(.document-filters > label) {
    flex: 1 1 100px;
  }
  .purchase-worklist :deep(.document-filters .document-keyword) {
    flex-basis: 100%;
  }
  .purchase-table-area {
    padding: 0 8px;
  }
}
</style>
