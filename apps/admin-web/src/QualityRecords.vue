<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { ApiError, request, session } from "./api";
import { activeEntity } from "./entityScope";
import { localTime } from "./sales";
import { showRecord } from "./workspace";
import { registerCatalogChanges } from "./catalogChanges";
import { businessToday, moveDay } from "./dates";
import DateInput from "./DateInput.vue";
const emit = defineEmits<{ failure: [error: unknown] }>();
interface Row {
  id: string;
  inspection_id: string;
  inspected_at: string;
  created_by: string;
  note: string;
  receipt_number: string;
  order_id: string;
  order_number: string;
  party_name: string;
  warehouse_name: string;
  product_name: string;
  product_code: string;
  specification: string;
  lot_no: string;
  mfg_date: string | null;
  expiry_date: string | null;
  base_unit: string;
  qualified_qty: string;
  rejected_qty: string;
  result: string;
  history_complete: boolean;
}
interface Report {
  items: Row[];
  has_more: boolean;
  total: number;
  inspections: number;
  incomplete: number;
  generated_at: string;
  can_open_source: boolean;
  export_limit: number;
  totals: { base_unit: string; qualified_qty: string; rejected_qty: string }[];
  warehouses: { id: string; name: string; archived: boolean }[];
}
const empty = (): Report => ({
  items: [],
  has_more: false,
  total: 0,
  inspections: 0,
  incomplete: 0,
  generated_at: "",
  can_open_source: false,
  export_limit: 10000,
  totals: [],
  warehouses: [],
});
const blank = () => ({
  search: "",
  supplier: "",
  product: "",
  lot_no: "",
  warehouse_id: "",
  result: "",
  date_from: "",
  date_to: "",
});
const filters = ref(blank()),
  applied = ref(blank()),
  offset = ref(0),
  loading = ref(false),
  exporting = ref(false),
  failed = ref(false),
  data = ref<Report>(empty());
const dirty = computed(
  () => JSON.stringify(filters.value) !== JSON.stringify(applied.value),
);
const results: Record<string, string> = {
  QUALIFIED: "全部合格",
  MIXED: "部分拒收",
  REJECTED: "全部拒收",
};
let epoch = 0,
  alive = true;
onUnmounted(() => {
  ++epoch;
  alive = false;
});
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: false, busy: exporting.value }),
    "quality-records",
  ),
);
function parameters() {
  return new URLSearchParams({
    entity_id: activeEntity.value,
    ...Object.fromEntries(
      Object.entries(applied.value).filter(([, value]) => value),
    ),
  });
}
async function load() {
  const token = ++epoch;
  loading.value = true;
  failed.value = false;
  const params = parameters();
  params.set("offset", String(offset.value));
  try {
    const result = await request<Report>("/api/v1/quality-records?" + params);
    if (token === epoch) data.value = result;
  } catch (error) {
    if (token === epoch) {
      failed.value = true;
      data.value = empty();
      emit("failure", error);
    }
  } finally {
    if (token === epoch) loading.value = false;
  }
}
async function query() {
  applied.value = { ...filters.value };
  offset.value = 0;
  await load();
}
async function reset() {
  filters.value = blank();
  await query();
}
async function recent() {
  filters.value.date_to = businessToday();
  filters.value.date_from = moveDay(filters.value.date_to, -29);
  await query();
}
async function exportCsv() {
  if (exporting.value || loading.value || dirty.value || !session.value) return;
  const context = session.value,
    entity = activeEntity.value,
    token = epoch;
  exporting.value = true;
  try {
    const response = await fetch(
      "/api/v1/quality-records/export?" + parameters(),
      {
        credentials: "same-origin",
        headers: { "X-Context-Generation": context.context_generation },
      },
    );
    if (!response.ok) {
      const body = await response.json();
      throw new ApiError(
        response.status,
        body.error?.code || "EXPORT_FAILED",
        body.error?.message || "导出失败，请重试",
      );
    }
    const blob = await response.blob();
    if (
      !alive ||
      session.value !== context ||
      activeEntity.value !== entity ||
      token !== epoch
    )
      return;
    const filename = decodeURIComponent(
      (response.headers.get("Content-Disposition") || "").split("UTF-8''")[1] ||
        "采购验收记录.csv",
    );
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    ElMessage.success(
      "已导出 " + (response.headers.get("X-Export-Rows") || "") + " 条验收明细",
    );
  } catch (error) {
    if (alive && session.value === context) emit("failure", error);
  } finally {
    exporting.value = false;
  }
}
watch(activeEntity, () => {
  filters.value = blank();
  applied.value = blank();
  offset.value = 0;
  data.value = empty();
  void load();
});
onMounted(load);
</script>
<template>
  <section
    class="panel quality-records"
    aria-label="质量记录"
    v-loading="loading"
  >
    <header class="master-toolbar">
      <div>
        <h2>质量记录 · 采购验收记录</h2>
        <small>按本次验收明细查询，数量为基本单位；记录在此只读查看。</small>
      </div>
      <el-button
        :loading="exporting"
        :disabled="loading || failed || dirty || !data.total"
        @click="exportCsv"
        >导出筛选结果 CSV</el-button
      >
    </header>
    <form class="quality-filters" @submit.prevent="query" data-date-query>
      <fieldset :disabled="loading || exporting">
        <label
          >综合查询<input
            v-model="filters.search"
            maxlength="100"
            placeholder="单号、随货单号、商品、批号或意见"
        /></label>
        <label
          >验收日期从<DateInput
            v-model="filters.date_from"
            query
            :max="filters.date_to || undefined"
        /></label>
        <label
          >验收日期至<DateInput
            v-model="filters.date_to"
            query
            :min="filters.date_from || undefined"
        /></label>
        <label
          >供应商<input
            v-model="filters.supplier"
            maxlength="100"
            placeholder="审核时名称"
        /></label>
        <label
          >商品<input
            v-model="filters.product"
            maxlength="100"
            placeholder="订购时名称、编码或规格"
        /></label>
        <label>批号<input v-model="filters.lot_no" maxlength="100" /></label>
        <label
          >仓库<select v-model="filters.warehouse_id">
            <option value="">全部仓库</option>
            <option v-for="w in data.warehouses" :key="w.id" :value="w.id">
              {{ w.name }}{{ w.archived ? "（已停用）" : "" }}
            </option>
          </select></label
        >
        <label
          >本次行验收结果<select v-model="filters.result">
            <option value="">全部结果</option>
            <option
              v-for="(label, value) in results"
              :key="value"
              :value="value"
            >
              {{ label }}
            </option>
            <option value="HAS_REJECTED">含拒收数量</option>
          </select></label
        >
      </fieldset>
      <div class="filter-actions">
        <el-button
          native-type="submit"
          type="primary"
          :disabled="loading || exporting"
          >查询</el-button
        ><el-button :disabled="loading || exporting" @click="reset"
          >重置</el-button
        ><el-button :disabled="loading || exporting" @click="recent"
          >近30天</el-button
        ><small v-if="dirty">筛选已修改，请查询后翻页或导出。</small>
      </div>
    </form>
    <p v-if="failed" role="alert">
      记录读取失败，请重新查询；未提供过期的查询结果。
    </p>
    <div v-else class="quality-summary" aria-label="筛选结果汇总">
      <strong>{{ data.inspections }} 次验收 · {{ data.total }} 条明细</strong>
      <span v-for="unit in data.totals" :key="unit.base_unit"
        >{{ unit.base_unit }}：合格 {{ unit.qualified_qty }} / 拒收
        {{ unit.rejected_qty }}</span
      >
      <small v-if="data.generated_at"
        >查询时间 {{ localTime(data.generated_at) }}</small
      >
    </div>
    <p class="form-note">
      名称与商品信息取自原采购单留存；未留存的历史不使用当前档案补写。导出包含全部筛选明细，最多
      {{ data.export_limit }} 行，按导出时的数据生成。CSV
      的编码、批号及数值按文本保护，保留精度。
    </p>
    <p v-if="data.incomplete" role="status" class="history-note">
      其中
      {{ data.incomplete }} 条明细的关键历史信息未完整留存，可查看原单核对。
    </p>
    <div class="master-table-scroll">
      <table class="master-list-table" aria-label="采购验收明细">
        <thead>
          <tr>
            <th>验收时间</th>
            <th>采购单 / 收货单</th>
            <th>供应商 / 仓库</th>
            <th>商品 / 编码</th>
            <th>规格</th>
            <th>批号</th>
            <th>生产日期 / 有效期至</th>
            <th>基本单位</th>
            <th>合格数量</th>
            <th>拒收数量</th>
            <th>本次行结果</th>
            <th>验收意见</th>
            <th>验收人账号标识</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in data.items" :key="r.id">
            <td>{{ localTime(r.inspected_at) }}</td>
            <td>
              <button
                v-if="data.can_open_source"
                class="source-link"
                @click="
                  showRecord(
                    'purchase',
                    r.order_id,
                    activeEntity,
                    r.order_number,
                  )
                "
                :disabled="exporting"
                aria-label="查看原采购单"
              >
                {{ r.order_number }}</button
              ><span v-else>{{ r.order_number }}</span
              ><small>{{ r.receipt_number }}</small>
            </td>
            <td>
              {{ r.party_name }}<small>{{ r.warehouse_name }}</small>
            </td>
            <td>
              {{ r.product_name
              }}<small>{{ r.product_code || "历史未留存" }}</small
              ><small v-if="!r.history_complete" class="history-note"
                >关键历史信息不完整</small
              >
            </td>
            <td>{{ r.specification || "—" }}</td>
            <td>{{ r.lot_no }}</td>
            <td>
              {{ r.mfg_date || "—" }}<small>{{ r.expiry_date || "—" }}</small>
            </td>
            <td>{{ r.base_unit }}</td>
            <td class="numeric">{{ r.qualified_qty }}</td>
            <td class="numeric">{{ r.rejected_qty }}</td>
            <td>
              <span :class="{ rejected: r.result !== 'QUALIFIED' }">{{
                results[r.result]
              }}</span>
            </td>
            <td class="note-cell">{{ r.note }}</td>
            <td>
              <span class="actor-id" :title="r.created_by">{{
                r.created_by
              }}</span>
            </td>
          </tr>
          <tr v-if="!data.items.length">
            <td colspan="13" class="master-empty-tab">
              {{ failed ? "查询失败，请重试" : "没有符合筛选条件的验收记录" }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="master-pagination">
      <el-button
        :disabled="!offset || loading || exporting || dirty"
        @click="
          offset -= 50;
          load();
        "
        >上一页</el-button
      ><span
        >第 {{ Math.floor(offset / 50) + 1 }} /
        {{ Math.max(1, Math.ceil(data.total / 50)) }} 页 · 每页50条</span
      ><el-button
        :disabled="
          !data.has_more || offset >= 100000 || loading || exporting || dirty
        "
        @click="
          offset += 50;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
</template>
<style scoped>
.quality-filters fieldset {
  display: grid;
  grid-template-columns: repeat(4, minmax(160px, 1fr));
  gap: 12px;
  border: 0;
  margin: 0;
  padding: 0;
}
.quality-filters label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
}
.quality-filters input,
.quality-filters select {
  width: 100%;
  min-width: 0;
  height: 34px;
  padding: 5px 8px;
  color: var(--ui-text);
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: 5px;
}
.filter-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 14px 0;
}
.filter-actions small {
  color: var(--ui-muted);
}
.quality-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  padding: 14px;
  background: var(--ui-tint);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
}
.quality-summary span {
  font-size: 13px;
}
.quality-summary small {
  margin-left: auto;
  color: var(--ui-muted);
}
.quality-records table {
  min-width: 1640px;
}
.quality-records td {
  vertical-align: top;
}
.quality-records td small {
  display: block;
  color: var(--ui-muted);
  margin-top: 4px;
}
.numeric {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.source-link {
  border: 0;
  background: none;
  color: var(--ui-accent);
  cursor: pointer;
  padding: 0;
  text-align: left;
  font: inherit;
}
.source-link:hover {
  text-decoration: underline;
}
.actor-id {
  display: block;
  max-width: 140px;
  overflow-wrap: anywhere;
  font-size: 11px;
}
.note-cell {
  max-width: 280px;
  white-space: normal;
  overflow-wrap: anywhere;
}
.history-note,
.rejected {
  color: #9a6414;
}
@media (max-width: 1000px) {
  .quality-filters fieldset {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 500px) {
  .quality-filters fieldset {
    grid-template-columns: minmax(0, 1fr);
  }
  .quality-summary small {
    margin-left: 0;
  }
}
</style>
