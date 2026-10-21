<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { ApiError, request, session } from "./api";
import { activeEntity } from "./entityScope";
import { goPage, reportRoute, setReportRoute } from "./workspace";
import {
  reportKinds,
  blankReportFilters,
  type ReportKind,
  type ReportField,
  type ReportRow,
  type ReportData,
} from "./documentReports";
import { localTime } from "./sales";
import { businessToday, moveDay } from "./dates";
import DateInput from "./DateInput.vue";
import ReportChart from "./ReportChart.vue";
const narrowQuery = window.matchMedia("(max-width: 799px)");
const narrow = ref(narrowQuery.matches);
const updateNarrow = () => {
  narrow.value = narrowQuery.matches;
};
onMounted(() => narrowQuery.addEventListener("change", updateNarrow));
onUnmounted(() => narrowQuery.removeEventListener("change", updateNarrow));
const props = defineProps<{ page: string; active: boolean }>();
const emit = defineEmits<{ failure: [unknown] }>();
const allowed = computed<ReportKind[]>(() =>
  props.page === "sales-reports"
    ? ["sale", "outbound"]
    : props.page === "quality-records"
      ? ["inspection"]
      : ["purchase", "receipt", "inspection", "inbound"],
);
const kind = ref<ReportKind>(allowed.value[0]);
const tab = ref("summary"),
  offset = ref(0),
  filters = ref(blankReportFilters()),
  applied = ref(blankReportFilters());
const data = ref<ReportData>(),
  loading = ref(false),
  failed = ref(false),
  exporting = ref(false);
const dimension = ref("day"),
  metric = ref(props.page === "quality-records" ? "documents" : "gross_amount"),
  unit = ref(""),
  chartType = ref("bar");
const columns = ref<ReportField[]>([]),
  columnDraft = ref<ReportField[]>([]),
  columnDialog = ref(false);
const related = ref<
    {
      kind: ReportKind;
      name: string;
      items: ReportRow[];
      has_more: boolean;
      order_id: string;
    }[]
  >([]),
  relatedOpen = ref(false),
  relatedLoading = ref(false),
  relatedFailed = ref(false),
  relatedDocument = ref("");
let epoch = 0,
  relationEpoch = 0,
  alive = true;
const view = computed(() => (tab.value === "detail" ? "detail" : "summary"));
const dirty = computed(
  () => JSON.stringify(filters.value) !== JSON.stringify(applied.value),
);
const visible = computed(() => columns.value.filter((c) => c.visible));
const title = computed(() =>
  props.page === "quality-records"
    ? "质量记录查询"
    : props.page === "sales-reports"
      ? "销售记录查询"
      : "采购记录查询",
);
const metricNames: Record<string, string> = {
  gross_amount: "含税金额",
  net_amount: "未税金额",
  tax_amount: "税额",
  documents: "单据数",
  lines: "明细条数",
  qty: "数量",
};
const metrics = computed(() =>
  Object.entries(metricNames).filter(
    ([key]) =>
      data.value?.money_visible ||
      !["gross_amount", "net_amount", "tax_amount"].includes(key),
  ),
);
const display = (row: ReportRow, key: string) =>
  key === "occurred_at"
    ? localTime(String(row[key]))
    : key === "state"
      ? data.value?.states[String(row[key])] || row[key]
      : key === "history_complete"
        ? row[key]
          ? "已留存"
          : "部分历史未留存／草稿"
        : key === "tax_rate"
          ? `${Number(row[key]) * 100}%`
          : (row[key] ?? "—");
function columnKey() {
  return `medinv.report.columns.v1:${session.value?.tenant_id}:${session.value?.principal_id}:${activeEntity.value}:${kind.value}:${view.value}`;
}
function loadColumns(fields: ReportField[]) {
  const available = new Map(fields.map((f) => [f.key, f]));
  const next: ReportField[] = [];
  try {
    const saved: unknown = JSON.parse(
      localStorage.getItem(columnKey()) || "null",
    );
    if (Array.isArray(saved))
      for (const raw of saved) {
        const f = available.get(raw?.key);
        if (!f) continue;
        next.push({
          ...f,
          visible: f.required || raw.visible !== false,
          width: Number.isFinite(raw.width)
            ? Math.max(80, Math.min(600, Math.round(raw.width)))
            : f.width,
        });
        available.delete(f.key);
      }
  } catch {
    /* Optional browser display preference. */
  }
  columns.value = [...next, ...available.values()];
}
function configureColumns() {
  columnDraft.value = columns.value.map((c) => ({ ...c }));
  columnDialog.value = true;
}
function moveColumn(index: number, delta: number) {
  const next = index + delta;
  if (next < 0 || next >= columnDraft.value.length) return;
  const [item] = columnDraft.value.splice(index, 1);
  columnDraft.value.splice(next, 0, item);
}
function saveColumns() {
  columns.value = columnDraft.value.map((c) => ({
    ...c,
    width: Math.max(80, Math.min(600, Number(c.width) || 150)),
    visible: c.required || c.visible,
  }));
  try {
    localStorage.setItem(columnKey(), JSON.stringify(columns.value));
    ElMessage.success("已保存当前浏览器的显示配置");
  } catch {
    ElMessage.info("浏览器未允许保存，当前页面配置仍已应用");
  }
  columnDialog.value = false;
}
function parameters() {
  return new URLSearchParams({
    entity_id: activeEntity.value,
    view: view.value,
    ...Object.fromEntries(Object.entries(applied.value).filter(([, v]) => v)),
    dimension: dimension.value,
    metric: metric.value,
    unit: unit.value,
  });
}
function syncRoute() {
  if (props.active)
    setReportRoute({
      page: props.page,
      entity: activeEntity.value,
      kind: kind.value,
      tab: tab.value,
      filters: { ...applied.value },
    });
}
async function load() {
  const token = ++epoch;
  loading.value = true;
  failed.value = false;
  data.value = undefined;
  const params = parameters();
  params.set("offset", String(offset.value));
  try {
    const result = await request<ReportData>(
      `/api/v1/document-reports/${kind.value}?${params}`,
    );
    if (token !== epoch) return;
    data.value = result;
    loadColumns(result.fields);
  } catch (error) {
    if (token === epoch) {
      failed.value = true;
      emit("failure", error);
    }
  } finally {
    if (token === epoch) loading.value = false;
  }
}
async function query() {
  applied.value = { ...filters.value };
  offset.value = 0;
  syncRoute();
  await load();
}
function reset() {
  filters.value = blankReportFilters();
  void query();
}
function recent() {
  filters.value.date_to = businessToday();
  filters.value.date_from = moveDay(filters.value.date_to, -29);
  void query();
}
function tabKeys(event: KeyboardEvent) {
  if (
    !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) ||
    loading.value ||
    exporting.value
  )
    return;
  event.preventDefault();
  const keys = ["summary", "detail", "charts"];
  const index =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? 2
        : (keys.indexOf(tab.value) + (event.key === "ArrowRight" ? 1 : 2)) % 3;
  switchTab(keys[index]);
  (event.currentTarget as HTMLElement)
    .querySelectorAll<HTMLButtonElement>("button")
    [index]?.focus();
}
function switchTab(value: string) {
  if (tab.value === value) return;
  tab.value = value;
  offset.value = 0;
  syncRoute();
  void load();
}
function switchKind() {
  filters.value = blankReportFilters();
  applied.value = blankReportFilters();
  offset.value = 0;
  unit.value = "";
  relatedOpen.value = false;
  relationEpoch++;
  syncRoute();
  void load();
}
function drill(row: ReportRow) {
  filters.value = { ...applied.value, document_id: row.document_id };
  tab.value = "detail";
  void query();
}
function clearDocument() {
  filters.value = { ...applied.value, document_id: "", order_id: "" };
  void query();
}
async function showRelated(row: ReportRow) {
  relatedDocument.value = row.document_id;
  relatedOpen.value = true;
  await loadRelated();
}
async function loadRelated() {
  const token = ++relationEpoch;
  relatedLoading.value = true;
  relatedFailed.value = false;
  related.value = [];
  try {
    const result = await request<{ groups: typeof related.value }>(
      `/api/v1/document-reports/${kind.value}/${relatedDocument.value}/related?entity_id=${activeEntity.value}`,
    );
    if (token === relationEpoch) related.value = result.groups;
  } catch (error) {
    if (token === relationEpoch) {
      relatedFailed.value = true;
      emit("failure", error);
    }
  } finally {
    if (token === relationEpoch) relatedLoading.value = false;
  }
}
function relatedDetail(group: (typeof related.value)[number], row?: ReportRow) {
  if (!allowed.value.includes(group.kind)) {
    relatedOpen.value = false;
    relationEpoch++;
    const targetPage = ["sale", "outbound"].includes(group.kind)
      ? "sales-reports"
      : "purchase-reports";
    goPage(targetPage);
    setReportRoute({
      page: targetPage,
      entity: activeEntity.value,
      kind: group.kind,
      tab: "detail",
      filters: {
        ...blankReportFilters(),
        ...(row
          ? { document_id: row.document_id }
          : { order_id: group.order_id }),
      },
    });
    return;
  }
  relatedOpen.value = false;
  relationEpoch++;
  kind.value = group.kind;
  tab.value = "detail";
  filters.value = {
    ...blankReportFilters(),
    ...(row ? { document_id: row.document_id } : { order_id: group.order_id }),
  };
  void query();
}
async function exportCsv() {
  if (
    !data.value ||
    failed.value ||
    dirty.value ||
    loading.value ||
    exporting.value ||
    !session.value
  )
    return;
  const current = session.value,
    entity = activeEntity.value,
    token = epoch;
  exporting.value = true;
  const params = parameters();
  params.set("columns", visible.value.map((c) => c.key).join(","));
  try {
    const response = await fetch(
      `/api/v1/document-reports/${kind.value}/export?${params}`,
      {
        credentials: "same-origin",
        headers: { "X-Context-Generation": current.context_generation },
      },
    );
    if (!response.ok) {
      const body = await response.json();
      throw new ApiError(
        response.status,
        body.error?.code || "EXPORT_FAILED",
        body.error?.message || "导出失败",
      );
    }
    const blob = await response.blob();
    if (
      !alive ||
      session.value !== current ||
      entity !== activeEntity.value ||
      token !== epoch
    )
      return;
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = decodeURIComponent(
      (response.headers.get("Content-Disposition") || "").split("UTF-8''")[1] ||
        "记录查询.csv",
    );
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    ElMessage.success(
      `已导出全部筛选结果 ${response.headers.get("X-Export-Rows")} 行`,
    );
  } catch (error) {
    if (alive && session.value === current) emit("failure", error);
  } finally {
    if (alive) exporting.value = false;
  }
}
function receiveRoute() {
  const route = reportRoute.value;
  if (route?.page !== props.page || route.entity !== activeEntity.value)
    return false;
  const nextKind = allowed.value.includes(route.kind as ReportKind)
    ? (route.kind as ReportKind)
    : allowed.value[0];
  const nextTab = ["summary", "detail", "charts"].includes(route.tab)
    ? route.tab
    : "summary";
  const nextFilters = {
    ...blankReportFilters(),
    ...Object.fromEntries(
      Object.entries(route.filters).filter(
        ([k, v]) =>
          k in blankReportFilters() && typeof v === "string" && v.length <= 100,
      ),
    ),
  };
  if (
    kind.value === nextKind &&
    tab.value === nextTab &&
    JSON.stringify(applied.value) === JSON.stringify(nextFilters)
  )
    return false;
  kind.value = nextKind;
  tab.value = nextTab;
  filters.value = nextFilters;
  applied.value = { ...nextFilters };
  offset.value = 0;
  return true;
}
watch(reportRoute, () => {
  if (props.active && receiveRoute()) void load();
});
watch(
  () => props.active,
  (active) => {
    if (active) {
      if (receiveRoute()) void load();
      syncRoute();
    }
  },
);
watch(activeEntity, () => {
  ++relationEpoch;
  relatedOpen.value = false;
  columnDialog.value = false;
  filters.value = blankReportFilters();
  applied.value = blankReportFilters();
  kind.value = allowed.value[0];
  offset.value = 0;
  syncRoute();
  void load();
});
onMounted(() => {
  receiveRoute();
  syncRoute();
  void load();
});
onUnmounted(() => {
  alive = false;
  ++epoch;
  ++relationEpoch;
});
</script>
<template>
  <section class="document-reports panel" :aria-label="title">
    <header class="report-heading">
      <div>
        <h2>{{ title }}</h2>
        <small>单据汇总、行级明细和图表使用同一组查询条件</small>
      </div>
      <label class="report-kind"
        >记录类型<select
          v-model="kind"
          aria-label="记录类型"
          :disabled="loading || exporting"
          @change="switchKind"
        >
          <option v-for="value in allowed" :key="value" :value="value">
            {{ reportKinds[value] }}
          </option>
        </select></label
      >
    </header>
    <form class="report-filters" data-date-query @submit.prevent="query">
      <fieldset :disabled="loading || exporting">
        <label
          >综合查询<input
            v-model="filters.search"
            maxlength="100"
            placeholder="单据号、商品、批号或备注"
        /></label>
        <label
          >发生日期从<DateInput
            v-model="filters.date_from"
            query
            :max="filters.date_to || undefined"
        /></label>
        <label
          >发生日期至<DateInput
            v-model="filters.date_to"
            query
            :min="filters.date_from || undefined"
        /></label>
        <label
          >往来单位<input
            v-model="filters.party"
            maxlength="100"
            placeholder="历史单据中的单位名称"
        /></label>
        <label
          >商品<input
            v-model="filters.product"
            maxlength="100"
            placeholder="名称、编码或规格"
        /></label>
        <label
          >仓库<input
            v-model="filters.warehouse"
            maxlength="100"
            placeholder="仓库名称"
        /></label>
        <label v-if="!['purchase', 'sale'].includes(kind)"
          >批号<input v-model="filters.lot_no" maxlength="100"
        /></label>
        <label
          >状态／结果<select aria-label="状态／结果" v-model="filters.state">
            <option value="">全部</option>
            <option
              v-for="(label, key) in data?.states || {}"
              :key="key"
              :value="key"
            >
              {{ label }}
            </option>
          </select></label
        >
        <div class="report-query-actions">
          <el-button type="primary" native-type="submit" :loading="loading"
            >查询</el-button
          ><el-button @click="recent">近30天</el-button
          ><el-button @click="reset">重置</el-button>
        </div>
      </fieldset>
    </form>
    <div v-if="applied.document_id || applied.order_id" class="report-drill">
      <span
        >已定位{{ applied.document_id ? "单据" : "来源订单" }}：{{
          data?.items[0]?.number || applied.document_id || applied.order_id
        }}</span
      ><el-button
        link
        type="primary"
        :disabled="loading || exporting"
        @click="clearDocument"
        >解除单据限定</el-button
      >
    </div>
    <div class="report-tabbar">
      <nav role="tablist" aria-label="记录查询视图" @keydown="tabKeys">
        <button
          v-for="item in [
            { key: 'summary', name: '单据汇总' },
            { key: 'detail', name: '单据明细' },
            { key: 'charts', name: '图形化报表' },
          ]"
          :key="item.key"
          role="tab"
          :aria-selected="tab === item.key"
          :tabindex="tab === item.key ? 0 : -1"
          :disabled="loading || exporting"
          @click="switchTab(item.key)"
        >
          {{ item.name }}
        </button>
      </nav>
      <div v-if="tab !== 'charts'" class="report-table-tools">
        <el-button :disabled="!data || loading" @click="configureColumns"
          >配置字段</el-button
        ><el-button
          :disabled="!data?.total || loading || failed || dirty"
          :loading="exporting"
          @click="exportCsv"
          >导出全部筛选结果</el-button
        >
      </div>
    </div>
    <p v-if="dirty" class="report-notice" role="status">
      筛选条件尚未应用，下方仍为上次查询结果；点击“查询”后更新。
    </p>
    <el-alert
      v-if="failed"
      title="记录查询失败，未展示旧数据；请重新查询。"
      type="error"
      :closable="false"
    />
    <div
      v-loading="loading"
      class="report-results"
      role="tabpanel"
      :aria-label="
        tab === 'summary'
          ? '单据汇总'
          : tab === 'detail'
            ? '单据明细'
            : '图形化报表'
      "
    >
      <template v-if="data">
        <div class="report-metrics">
          <div>
            <small>单据数</small><strong>{{ data.totals.documents }}</strong>
          </div>
          <div>
            <small>明细条数</small><strong>{{ data.totals.lines }}</strong>
          </div>
          <div>
            <small>商品种数</small><strong>{{ data.totals.products }}</strong>
          </div>
          <div v-if="data.money_visible">
            <small
              >{{
                ["receipt", "inspection"].includes(kind)
                  ? "参考含税金额"
                  : "含税金额"
              }}（元）</small
            ><strong>{{ data.totals.gross_amount }}</strong>
          </div>
          <div v-if="data.money_visible">
            <small>未税金额 / 税额（元）</small
            ><strong
              >{{ data.totals.net_amount }} /
              {{ data.totals.tax_amount }}</strong
            >
          </div>
        </div>
        <div class="report-context">
          <span
            >{{
              data.money_visible ? data.amount_basis + "。" : ""
            }}汇总仅统计符合条件的明细，数量按基本单位分开计算。</span
          ><span>查询时间：{{ localTime(data.generated_at) }}</span>
        </div>
        <p
          v-if="kind === 'inspection' || kind === 'inbound'"
          class="report-caption"
        >
          {{ data.number_basis }}
        </p>
        <p v-if="Number(data.totals.incomplete) > 0" class="report-notice">
          {{ data.totals.incomplete }}
          条明细的部分历史信息未留存或属于未审核草稿；缺失历史不以今天的主档替代。
        </p>
        <template v-if="tab !== 'charts'">
          <el-table
            :data="data.items"
            row-key="id"
            border
            :max-height="'calc(var(--ui-workspace-height, 900px) - 390px)'"
            empty-text="没有符合条件的记录"
            class="report-table"
          >
            <el-table-column
              v-for="column in visible"
              :key="column.key"
              :prop="column.key"
              :label="column.label"
              :width="column.width"
              :fixed="!narrow && column.key === 'number' ? 'left' : undefined"
              show-overflow-tooltip
            >
              <template #default="{ row }"
                ><el-button
                  v-if="column.key === 'number' && tab === 'summary'"
                  link
                  type="primary"
                  @click="drill(row)"
                  >{{ row.number }}</el-button
                ><span v-else>{{ display(row, column.key) }}</span></template
              >
            </el-table-column>
            <el-table-column
              v-if="tab === 'summary'"
              label="操作"
              width="135"
              :fixed="narrow ? undefined : 'right'"
              ><template #default="{ row }"
                ><el-button link type="primary" @click="showRelated(row)"
                  >查看关联记录</el-button
                ></template
              ></el-table-column
            >
          </el-table>
          <footer class="report-pager">
            <span
              >共 {{ data.total }}
              {{ tab === "summary" ? "张单据" : "条明细" }} · 第
              {{ Math.floor(offset / 50) + 1 }} 页</span
            ><el-button
              :disabled="!offset || loading || exporting"
              @click="
                offset -= 50;
                load();
              "
              >上一页</el-button
            ><el-button
              :disabled="!data.has_more || loading || exporting"
              @click="
                offset += 50;
                load();
              "
              >下一页</el-button
            >
          </footer>
        </template>
        <template v-else>
          <div class="report-chart-controls">
            <label
              >分组维度<select
                aria-label="分组维度"
                v-model="dimension"
                :disabled="loading || dirty"
                @change="load"
              >
                <option value="day">按日</option>
                <option value="month">按月</option>
                <option value="party">往来单位</option>
                <option value="warehouse">仓库</option>
                <option value="product">商品</option>
                <option value="state">状态／结果</option>
              </select></label
            ><label
              >统计指标<select
                aria-label="统计指标"
                v-model="metric"
                :disabled="loading || dirty"
                @change="load"
              >
                <option v-for="[key, label] in metrics" :key="key" :value="key">
                  {{ label }}
                </option>
              </select></label
            ><label v-if="metric === 'qty'"
              >基本单位<select
                aria-label="基本单位"
                v-model="unit"
                :disabled="loading || dirty"
                @change="load"
              >
                <option value="">请选择单位</option>
                <option v-for="value in data.units" :key="value.base_unit">
                  {{ value.base_unit }}
                </option>
              </select></label
            ><label
              >图形样式<select aria-label="图形样式" v-model="chartType">
                <option value="bar">条形图</option>
                <option value="line">折线图</option>
                <option value="donut">环形图</option>
                <option value="table">数据表</option>
              </select></label
            >
          </div>
          <ReportChart
            :items="data.chart.items"
            :type="chartType"
            :unit="data.chart.unit || ''"
            :metric-label="metricNames[data.chart.metric]"
            :states="data.states"
          />
          <p class="report-caption">{{ data.chart.note }}</p>
        </template>
        <div class="report-unit-totals">
          <span v-for="value in data.units" :key="value.base_unit"
            >{{ value.base_unit }}：{{ value.qty
            }}<template v-if="kind === 'inspection'"
              >（合格 {{ value.qualified_qty }} / 拒收
              {{ value.rejected_qty }}）</template
            ></span
          >
        </div>
      </template>
    </div>
    <el-drawer
      v-model="columnDialog"
      title="配置查询字段"
      size="min(640px, 95vw)"
      append-to-body
      ><p>
        配置仅影响当前单据类型及汇总／明细视图，保存在当前浏览器。单据号保留以便定位。
      </p>
      <div
        v-for="(column, index) in columnDraft"
        :key="column.key"
        class="report-column-row"
      >
        <label
          ><input
            v-model="column.visible"
            type="checkbox"
            :disabled="column.required"
          />{{ column.label }}</label
        ><label
          >宽度<input
            v-model.number="column.width"
            type="number"
            min="80"
            max="600"
            :aria-label="column.label + '宽度'" /></label
        ><el-button
          :disabled="index === 0"
          :aria-label="column.label + '上移'"
          @click="moveColumn(index, -1)"
          >↑</el-button
        ><el-button
          :disabled="index === columnDraft.length - 1"
          :aria-label="column.label + '下移'"
          @click="moveColumn(index, 1)"
          >↓</el-button
        >
      </div>
      <template #footer
        ><el-button @click="columnDraft = data!.fields.map((c) => ({ ...c }))"
          >恢复默认</el-button
        ><el-button @click="columnDialog = false">取消</el-button
        ><el-button type="primary" @click="saveColumns"
          >保存配置</el-button
        ></template
      ></el-drawer
    >
    <el-drawer
      v-model="relatedOpen"
      title="查看关联记录"
      size="min(960px, 96vw)"
      append-to-body
      ><div v-loading="relatedLoading">
        <p>按来源订单列出相关单据；点击进入对应记录明细查询。</p>
        <el-alert
          v-if="relatedFailed"
          title="关联记录加载失败"
          type="error"
          :closable="false"
        /><el-button v-if="relatedFailed" @click="loadRelated">重试</el-button>
        <section
          v-for="group in related"
          :key="group.kind"
          class="report-related"
        >
          <h3>{{ group.name }}</h3>
          <el-table :data="group.items" empty-text="暂无此类记录"
            ><el-table-column label="单据号" min-width="245"
              ><template #default="{ row }"
                ><el-button
                  link
                  type="primary"
                  @click="relatedDetail(group, row)"
                  >{{ row.number }}</el-button
                ></template
              ></el-table-column
            ><el-table-column prop="occurred_at" label="发生时间" width="180"
              ><template #default="{ row }">{{
                localTime(row.occurred_at)
              }}</template></el-table-column
            ><el-table-column
              prop="line_count"
              label="明细条数"
              width="100" /></el-table
          ><el-button
            v-if="group.has_more"
            link
            type="primary"
            @click="relatedDetail(group)"
            >超过50张，查看该订单全部{{ group.name }}明细</el-button
          >
        </section>
      </div></el-drawer
    >
  </section>
</template>
<style scoped>
.document-reports {
  min-width: 0;
  padding: 18px;
}
.report-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 16px;
}
.report-heading h2 {
  margin: 0 0 4px;
  font-size: 20px;
}
.report-heading small,
.report-caption,
.report-context {
  color: var(--ui-muted);
}
.report-kind {
  display: flex;
  align-items: center;
  gap: 12px;
}
.report-filters fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}
.report-filters label,
.report-chart-controls label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
}
.document-reports input,
.document-reports select {
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  padding: 7px 9px;
  background: white;
  color: var(--ui-text);
  font: inherit;
}
.report-query-actions {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  grid-column: span 2;
}
.report-query-actions .el-button + .el-button {
  margin-left: 0;
}
.report-tabbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid var(--ui-border);
  margin-top: 18px;
  flex-wrap: wrap;
}
.report-tabbar nav {
  display: flex;
}
.report-tabbar nav button {
  border: 0;
  border-bottom: 3px solid transparent;
  background: transparent;
  font: inherit;
  cursor: pointer;
  padding: 13px 20px;
  color: var(--ui-muted);
}
.report-tabbar nav button[aria-selected="true"] {
  color: var(--ui-accent);
  border-color: var(--ui-accent);
  font-weight: 600;
  background: var(--ui-tint);
}
.report-tabbar button:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: -3px;
}
.report-table-tools {
  padding: 6px 0;
}
.report-results {
  min-height: 180px;
}
.report-metrics {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin: 16px 0 12px;
}
.report-metrics > div {
  border: 1px solid var(--ui-border);
  background: var(--ui-soft);
  padding: 10px 16px;
  border-radius: 7px;
  min-width: 110px;
  flex: 1;
}
.report-metrics small {
  display: block;
  color: var(--ui-muted);
  margin-bottom: 4px;
}
.report-metrics strong {
  font-size: 19px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.report-context {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.report-caption {
  font-size: 12px;
}
.report-notice,
.report-drill {
  padding: 9px 12px;
  background: #fff7e8;
  color: #825c15;
  border-radius: 5px;
  font-size: 13px;
}
.report-drill {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 12px;
  overflow-wrap: anywhere;
}
.report-pager {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: flex-end;
  padding: 12px 0;
}
.report-pager > span {
  margin-right: auto;
  color: var(--ui-muted);
}
.report-unit-totals {
  display: flex;
  gap: 15px;
  flex-wrap: wrap;
  padding-top: 10px;
  color: var(--ui-muted);
  font-size: 12px;
}
.report-chart-controls {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin: 18px 0;
}
.report-chart-controls label {
  min-width: 140px;
}
.report-column-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--ui-border);
}
.report-column-row > label:first-child {
  flex: 1;
}
.report-column-row input[type="number"] {
  width: 70px;
}
.report-related {
  margin: 20px 0;
}
@media (max-width: 700px) {
  .document-reports {
    padding: 12px;
  }
  .report-heading {
    flex-wrap: wrap;
  }
  .report-filters fieldset {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .report-query-actions {
    grid-column: 1/-1;
  }
  .report-metrics strong {
    font-size: 16px;
  }
  .report-tabbar nav {
    width: 100%;
  }
  .report-tabbar nav button {
    flex: 1;
    padding: 12px 8px;
    font-size: 13px;
  }
  .report-pager {
    flex-wrap: wrap;
  }
  .report-table {
    width: 100%;
  }
}
</style>
