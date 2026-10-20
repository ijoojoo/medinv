<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { activeEntity } from "./entityScope";
import { localTime } from "./sales";
import { useWorkbenchData, type RiskData } from "./workbench";
import type { WorkQueueItem, WorkbenchVisit } from "./workQueue";
import { workbenchReturn } from "./workspace";
import WorkbenchStatus from "./WorkbenchStatus.vue";
const props = defineProps<{ active: boolean }>();
const emit = defineEmits<{ open: [WorkbenchVisit] }>();
const { data, loading, error, load } = useWorkbenchData<RiskData>(
  () => `/api/v1/workbench/risks?entity_id=${activeEntity.value}`,
  () => props.active,
);
const period = ref(7),
  opened = ref(false),
  group = ref("all"),
  offset = ref(0),
  search = ref(""),
  query = ref("");
const detailElement = ref<HTMLElement>(),
  returning = ref<WorkbenchVisit | null>(null),
  returnNotice = ref(""),
  drawerReady = ref(false);
const itemKey = (item: WorkQueueItem) => `${item.kind}:${item.item_id}`;
let handledReturn = 0;
watch(
  [() => props.active, workbenchReturn],
  () => {
    const target = workbenchReturn.value;
    if (
      !props.active ||
      !target ||
      target.token === handledReturn ||
      target.visit.view !== "risk" ||
      target.visit.item.entity_id !== activeEntity.value
    )
      return;
    handledReturn = target.token;
    const filters = target.visit.filters;
    group.value = filters.group;
    search.value = filters.input;
    query.value = filters.search;
    offset.value = filters.offset;
    returning.value = target.visit;
    returnNotice.value = "";
    opened.value = true;
  },
  { flush: "sync" },
);
const {
  data: detail,
  loading: detailLoading,
  error: detailError,
  load: loadDetail,
} = useWorkbenchData<RiskData>(
  () =>
    "/api/v1/workbench/risks?" +
    new URLSearchParams({
      entity_id: activeEntity.value,
      group: group.value,
      offset: String(offset.value),
      search: query.value,
    }),
  () => props.active && opened.value,
);
watch([detail, drawerReady], async ([result, ready]) => {
  if (!result || !props.active || !opened.value) return;
  if (offset.value > 0 && offset.value >= result.total) {
    offset.value = Math.floor(Math.max(0, result.total - 1) / 20) * 20;
    return;
  }
  const origin = returning.value;
  if (!origin || !ready || detailLoading.value) return;
  const target =
    result.items.find((item) => itemKey(item) === itemKey(origin.item)) ||
    result.items[Math.min(origin.index, result.items.length - 1)];
  returnNotice.value = "已恢复原查询条件，风险事项已刷新。";
  await nextTick();
  if (!props.active || !opened.value || returning.value !== origin) return;
  const element = [
    ...(detailElement.value?.querySelectorAll<HTMLElement>(
      "[data-work-item]",
    ) || []),
  ].find((el) => el.dataset.workItem === (target && itemKey(target)));
  const focus =
    element?.querySelector<HTMLElement>("summary") ||
    detailElement.value?.querySelector<HTMLInputElement>(
      '[aria-label="检索风险事项"]',
    );
  focus?.focus({ preventScroll: true });
  focus?.scrollIntoView({ block: "nearest", inline: "nearest" });
  returning.value = null;
});
const names = computed(() =>
  Object.fromEntries((data.value?.kinds || []).map((k) => [k.code, k.name])),
);
const groupNames: Record<string, string> = {
  all: "全部已接入风险事项",
  quality: "质量处置",
  receipt: "采购拒收交还",
  returns: "退回品拒收交还",
  transit: "在途损失",
};
const scopeNames: Record<string, string> = {
  quality: "隔离库存处置",
  receipt: "采购到货与验收",
  returns: "客户退回品收验",
  transit: "库间调拨在途",
};
const maximum = computed(() =>
  Math.max(1, ...(data.value?.groups.map((g) => g.count) || [])),
);
function count(code: string) {
  return data.value
    ? (data.value.groups.find((g) => g.code === code)?.count ?? 0)
    : "—";
}
function open(code = "all") {
  returning.value = null;
  returnNotice.value = "";
  group.value = code;
  offset.value = 0;
  query.value = "";
  search.value = "";
  opened.value = true;
}
function searchNow() {
  returning.value = null;
  returnNotice.value = "";
  if (query.value === search.value.trim() && !offset.value) void loadDetail();
  else {
    query.value = search.value.trim();
    offset.value = 0;
  }
}
function changePage(value: number) {
  returning.value = null;
  returnNotice.value = "";
  offset.value = Math.max(0, value);
}
function visit(row: WorkQueueItem, index: number) {
  opened.value = false;
  emit("open", {
    item: row,
    index,
    view: "risk",
    filters: {
      group: group.value,
      search: query.value,
      input: search.value,
      offset: offset.value,
    },
  });
}
</script>
<template>
  <div class="wb-heading" style="margin-bottom: 14px; flex-wrap: wrap">
    <p class="wb-subtitle">已接入的异常处置事项 · 当前未闭环快照</p>
    <button class="wb-button" :disabled="loading" @click="load">
      刷新风险数据
    </button>
  </div>
  <WorkbenchStatus
    :loading="loading"
    :error="error"
    :has-data="!!data"
    @retry="load"
  />
  <section class="wb-kpis wb-four" aria-label="风险汇总">
    <div class="wb-kpi">
      <button :disabled="!data" @click="open()">
        <label>当前未闭环</label><strong>{{ data?.total_open ?? "—" }}</strong
        ><small>已授权、已接入事项合计 ↗</small>
      </button>
    </div>
    <div
      v-for="code in ['quality', 'receipt', 'transit']"
      :key="code"
      class="wb-kpi"
    >
      <button
        :disabled="!data?.groups.some((g) => g.code === code)"
        @click="open(code)"
      >
        <label>{{ groupNames[code] }}</label
        ><strong>{{
          data?.groups.some((g) => g.code === code) ? count(code) : "—"
        }}</strong
        ><small>合计中的分类事项 ↗</small>
      </button>
    </div>
  </section>
  <div class="wb-main-grid">
    <section class="wb-panel">
      <div class="wb-panel-heading">
        <h3>风险变化趋势</h3>
        <div class="wb-period" aria-label="风险趋势周期">
          <button
            v-for="n in [7, 14]"
            :key="n"
            :aria-pressed="period === n"
            @click="period = n"
          >
            近{{ n }}日
          </button>
        </div>
      </div>
      <div class="wb-unavailable">
        <strong>近{{ period }}日趋势尚未接入</strong>
        <p>
          现有单据可汇总当前待处理事项，但尚无统一的风险产生、解除与每日存量记录，暂不绘制历史趋势。
        </p>
        <p>
          风险优先级、分派责任人与超时规则也尚未统一；已有办理职责继续在原单中控制。
        </p>
      </div>
    </section>
    <section class="wb-panel" aria-label="风险类别分布">
      <div class="wb-panel-heading">
        <h3>风险类别分布</h3>
        <span class="wb-note">同一快照 · 按事项数</span>
      </div>
      <div v-for="g in data?.groups" :key="g.code" class="wb-stock-row">
        <button class="wb-link" @click="open(g.code)">
          {{ groupNames[g.code] }}
        </button>
        <div class="wb-bar">
          <i :style="{ width: (g.count / maximum) * 100 + '%' }"></i>
        </div>
        <strong>{{ g.count }}</strong>
      </div>
      <p v-if="data && !data.total_open" class="wb-empty">
        已接入范围内，当前没有未闭环事项。
      </p>
      <p class="wb-note" style="margin-top: 20px">
        各类别互斥，合计与上方未闭环数量一致。只展示有读取权限的类别。
      </p>
    </section>
  </div>
  <section class="wb-panel" aria-label="重点业务范围">
    <div class="wb-panel-heading">
      <h3>重点业务范围</h3>
      <button :disabled="!data" class="wb-link" @click="open()">
        查看全部事项 ↗
      </button>
    </div>
    <div class="wb-scroll">
      <table class="wb-table">
        <thead>
          <tr>
            <th>业务范围</th>
            <th>待处理事项</th>
            <th>已接入环节</th>
            <th>查看</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="g in data?.groups" :key="g.code">
            <td>{{ scopeNames[g.code] }}</td>
            <td>{{ g.count }}</td>
            <td>{{ g.kinds.map((k) => names[k] || k).join("、") }}</td>
            <td>
              <button class="wb-link" @click="open(g.code)">查看事项 ↗</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="data && !data.groups.length" class="wb-empty">
      当前账号没有已接入风险类别的读取权限。
    </p>
  </section>
  <div class="wb-foot">
    <span
      >覆盖质量处置、采购拒收、退回品拒收、在途损失；并非企业全部风险。{{
        data ? "截至 " + localTime(data.as_of) : ""
      }}</span
    >
  </div>
  <el-drawer
    v-model="opened"
    @open="drawerReady = false"
    @opened="drawerReady = true"
    @close="drawerReady = false"
    :title="groupNames[group]"
    class="wb-drawer"
    append-to-body
    ><div ref="detailElement" class="workbench">
      <form class="wb-search" @submit.prevent="searchNow">
        <input
          v-model="search"
          type="search"
          aria-label="检索风险事项"
          placeholder="单号、往来单位、仓库"
          maxlength="100"
        /><button class="wb-button" type="submit">查询事项</button>
      </form>
      <WorkbenchStatus
        :loading="detailLoading"
        :error="detailError"
        :has-data="!!detail"
        @retry="loadDetail"
      />
      <p v-if="returnNotice" role="status" class="wb-note">
        {{ returnNotice }}
      </p>
      <template v-if="detail"
        ><p class="wb-note">
          共 {{ detail.total }} 条 · 截至
          {{ localTime(detail.as_of) }}；展开事项后可前往原单办理。
        </p>
        <details
          v-for="(r, index) in detail.items"
          :key="r.item_id + ':' + r.kind"
          class="wb-risk-record"
          :data-work-item="itemKey(r)"
        >
          <summary>
            <span class="wb-tag">{{ names[r.kind] || r.kind }}</span>
            <b>{{ r.number }}</b
            ><small>{{
              r.party_name || r.warehouse_name || "业务异常处置"
            }}</small>
          </summary>
          <p v-if="r.related_number">关联单据：{{ r.related_number }}</p>
          <p>
            往来单位：{{ r.party_name || "—" }} · 仓库：{{
              r.warehouse_name || "—"
            }}
          </p>
          <p>单据时间：{{ localTime(r.document_time) }}</p>
          <p class="wb-note">
            责任分派与统一超时规则未接入。原单的权限、审核与办理规则继续生效。
          </p>
          <button class="wb-button" @click="visit(r, index)">
            查看原单并办理 ↗
          </button>
        </details>
        <p v-if="!detail.items.length" class="wb-empty">
          当前筛选范围暂无事项。
        </p>
        <div class="wb-pagination">
          <span>第 {{ offset / 20 + 1 }} 页 · 每页20条</span>
          <div>
            <button
              class="wb-button"
              :disabled="!offset || detailLoading"
              @click="changePage(offset - 20)"
            >
              上一页事项</button
            ><button
              class="wb-button"
              :disabled="!detail.has_more || detailLoading"
              @click="changePage(offset + 20)"
            >
              下一页事项
            </button>
          </div>
        </div></template
      >
    </div></el-drawer
  >
</template>
<style scoped>
.wb-risk-record {
  padding: 14px 0;
  border-bottom: 1px solid var(--ui-border);
}
.wb-risk-record summary {
  cursor: pointer;
  overflow-wrap: anywhere;
}
.wb-risk-record summary b {
  font-weight: 500;
}
.wb-risk-record summary small {
  display: block;
  margin: 5px 0;
  color: var(--ui-muted);
}
.wb-risk-record p {
  margin: 10px 0;
}
.wb-risk-record button {
  margin-top: 5px;
}
</style>
