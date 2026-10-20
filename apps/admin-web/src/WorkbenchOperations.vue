<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { activeEntity } from "./entityScope";
import { session } from "./api";
import { localTime } from "./sales";
import { createEditor, workbenchReturn } from "./workspace";
import WorkbenchDrafts from "./WorkbenchDrafts.vue";
import WorkbenchShortcuts from "./WorkbenchShortcuts.vue";
import type {
  WorkQueueFilters,
  WorkQueueItem,
  WorkbenchVisit,
} from "./workQueue";
import { can, useWorkbenchData, type QueueData } from "./workbench";
import WorkbenchStatus from "./WorkbenchStatus.vue";
const props = defineProps<{
  active: boolean;
  modelValue: WorkQueueFilters | null;
}>();
const emit = defineEmits<{
  open: [WorkbenchVisit];
  "update:modelValue": [WorkQueueFilters];
}>();
const saved =
  props.modelValue?.entity_id === activeEntity.value ? props.modelValue : null;
const kind = ref(saved?.kind || "all"),
  search = ref(saved?.input ?? saved?.search ?? ""),
  query = ref(saved?.search || ""),
  offset = ref(saved?.offset || 0),
  priority = ref(saved?.priority || false),
  limit = ref(saved?.limit || (saved?.offset ? 50 : 6));
const queueElement = ref<HTMLElement>(),
  returning = ref<WorkbenchVisit | null>(null),
  returnedKey = ref(""),
  returnNotice = ref("");
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
      target.visit.view !== "operations" ||
      target.visit.item.entity_id !== activeEntity.value
    )
      return;
    handledReturn = target.token;
    const filters = target.visit.filters;
    kind.value = filters.kind;
    search.value = filters.input ?? filters.search;
    query.value = filters.search;
    offset.value = filters.offset;
    priority.value = !!filters.priority;
    limit.value = filters.limit || 6;
    returning.value = target.visit;
    returnNotice.value = "";
    returnedKey.value = "";
  },
  { flush: "sync" },
);
const visible = computed(() =>
  [
    "purchase.read",
    "sales.read",
    "retail.read",
    "returns.read",
    "transfers.read",
    "counts.read",
    "inventory.read",
    "quality.read",
  ].some(can),
);
const { data, loading, error, load } = useWorkbenchData<QueueData>(
  () =>
    "/api/v1/work-queue?" +
    new URLSearchParams({
      entity_id: activeEntity.value,
      kind: kind.value,
      search: query.value,
      offset: String(offset.value),
      limit: String(limit.value),
      priority: String(priority.value),
    }),
  () => props.active && visible.value,
);
watch([kind, query, search, offset, priority, limit], () =>
  emit("update:modelValue", {
    entity_id: activeEntity.value,
    kind: kind.value,
    search: query.value,
    offset: offset.value,
    priority: priority.value,
    limit: limit.value,
    input: search.value,
  }),
);
watch(data, async (result) => {
  if (!result || !props.active) return;
  if (offset.value > 0 && offset.value >= result.total) {
    offset.value =
      Math.floor(Math.max(0, result.total - 1) / limit.value) * limit.value;
    return;
  }
  const origin = returning.value;
  if (!origin) return;
  const same = result.items.find(
    (item) => itemKey(item) === itemKey(origin.item),
  );
  const target =
    same || result.items[Math.min(origin.index, result.items.length - 1)];
  returnedKey.value = target ? itemKey(target) : "";
  returnNotice.value = same
    ? "已返回原待办，单据状态已刷新。"
    : "已刷新当前筛选下的待办，原事项已不在本页，请以最新列表为准。";
  await nextTick();
  if (!props.active || returning.value !== origin) return;
  const button = [
    ...(queueElement.value?.querySelectorAll<HTMLButtonElement>(
      "[data-work-item]",
    ) || []),
  ].find((element) => element.dataset.workItem === returnedKey.value);
  const focus =
    button ||
    queueElement.value?.querySelector<HTMLInputElement>(
      '[aria-label="查找待办"]',
    );
  focus?.focus({ preventScroll: true });
  focus?.scrollIntoView({ block: "nearest", inline: "nearest" });
  returning.value = null;
});
function visit(item: WorkQueueItem, index: number) {
  emit("open", {
    item,
    index,
    view: "operations",
    filters: {
      entity_id: activeEntity.value,
      kind: kind.value,
      search: query.value,
      input: search.value,
      offset: offset.value,
      priority: priority.value,
      limit: limit.value,
    },
  });
}
const names = computed(() =>
  Object.fromEntries((data.value?.kinds || []).map((k) => [k.code, k.name])),
);
const stages = [
  { kind: "purchase_receive", name: "待收货" },
  { kind: "purchase_inspect", name: "待验收" },
  { kind: "sales_post", name: "待出库" },
  { kind: "purchase_handback", name: "拒收待退回" },
];
function stage(value: string) {
  clearReturn();
  kind.value = value;
  offset.value = 0;
}
function searchNow() {
  clearReturn();
  if (query.value === search.value.trim() && !offset.value) void load();
  else {
    query.value = search.value.trim();
    offset.value = 0;
  }
}
function reset() {
  clearReturn();
  search.value = "";
  query.value = "";
  kind.value = "all";
  priority.value = false;
  offset.value = 0;
}
function all() {
  clearReturn();
  limit.value = 50;
  offset.value = 0;
}
function clearReturn() {
  returning.value = null;
  returnedKey.value = "";
  returnNotice.value = "";
}
function changePage(value: number) {
  clearReturn();
  offset.value = Math.max(0, value);
}
const draftsPanel = ref<InstanceType<typeof WorkbenchDrafts>>();
function refresh() {
  void load();
  void draftsPanel.value?.refresh();
}
</script>
<template>
  <div class="wb-heading" style="margin-bottom: 14px; flex-wrap: wrap">
    <p class="wb-subtitle">
      {{ session?.display_name }}，从当前主体的待办继续今天的业务。
    </p>
    <div class="wb-toolbar">
      <button class="wb-button" :disabled="loading" @click="refresh">
        刷新业务待办</button
      ><button
        v-if="can('purchase.write')"
        class="wb-button primary"
        @click="createEditor('purchase', activeEntity)"
      >
        ＋ 新建采购单
      </button>
    </div>
  </div>
  <section class="wb-kpis wb-four" aria-label="关键业务环节">
    <div v-for="s in stages" :key="s.kind" class="wb-kpi">
      <button
        :disabled="!names[s.kind]"
        :aria-pressed="kind === s.kind"
        @click="stage(s.kind)"
      >
        <label>{{ s.name }}</label
        ><strong>{{
          data && names[s.kind] ? data.counts[s.kind] || 0 : "—"
        }}</strong
        ><small>{{
          names[s.kind] ? "按当前检索范围汇总" : "未授权或尚未读取"
        }}</small>
      </button>
    </div>
  </section>
  <div class="wb-operations-grid">
    <section ref="queueElement" class="wb-panel" aria-label="进销存业务待办">
      <div class="wb-panel-heading">
        <h3>进销存业务待办</h3>
        <button class="wb-link" @click="reset">清空条件</button>
      </div>
      <form class="wb-search" @submit.prevent="searchNow">
        <input
          v-model="search"
          type="search"
          aria-label="查找待办"
          placeholder="单号、往来单位、仓库"
          maxlength="100"
        /><button class="wb-button" type="submit">查询待办</button
        ><select
          :value="kind"
          aria-label="待办环节"
          @change="stage(($event.target as HTMLSelectElement).value)"
        >
          <option value="all">全部环节</option>
          <option v-for="k in data?.kinds" :key="k.code" :value="k.code">
            {{ k.name }}
          </option></select
        ><label
          ><input
            v-model="priority"
            type="checkbox"
            @change="changePage(0)"
          />仅优先核对</label
        >
      </form>
      <p class="wb-note">
        优先核对指预占已过期的业务；卡片数量随检索和优先条件同步。
      </p>
      <WorkbenchStatus
        :loading="loading"
        :error="error"
        :has-data="!!data"
        @retry="load"
      />
      <p v-if="returnNotice" role="status" class="wb-note">
        {{ returnNotice }}
      </p>
      <div v-if="data?.items.length" class="wb-scroll">
        <table class="wb-table">
          <thead>
            <tr>
              <th>待办环节</th>
              <th>业务单据</th>
              <th>往来单位 / 仓库</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(r, index) in data.items"
              :key="itemKey(r)"
              :class="{ 'wb-returned-row': returnedKey === itemKey(r) }"
            >
              <td>
                <span class="wb-tag">{{ names[r.kind] || r.kind }}</span
                ><small v-if="r.reservation_expired" class="urgent"
                  >预占已过期，优先核对</small
                >
              </td>
              <td>
                <span class="number">{{ r.number }}</span
                ><small v-if="r.related_number"
                  >关联 {{ r.related_number }}</small
                ><small>{{ localTime(r.document_time) }}</small>
              </td>
              <td>
                {{ r.party_name || "—"
                }}<small>{{ r.warehouse_name || "—" }}</small>
              </td>
              <td>
                <button
                  class="wb-link"
                  :data-work-item="itemKey(r)"
                  @click="visit(r, index)"
                >
                  查看原单 ↗
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="!loading && !error" class="wb-empty">
        {{
          visible
            ? "当前筛选范围没有待办，可切换环节或清空条件。"
            : "当前账号没有业务待办读取权限。"
        }}
      </p>
      <div v-if="data" class="wb-pagination">
        <span>共 {{ data.total }} 条 · 本页 {{ data.items.length }} 条</span
        ><button
          v-if="limit === 6 && data.has_more"
          class="wb-link"
          @click="all"
        >
          查看全部待办 ↗
        </button>
        <div v-else>
          <button
            class="wb-button"
            :disabled="!offset || loading"
            @click="changePage(offset - limit)"
          >
            上一页待办</button
          ><button
            class="wb-button"
            :disabled="!data.has_more || loading"
            @click="changePage(offset + limit)"
          >
            下一页待办
          </button>
        </div>
      </div>
    </section>
    <aside class="wb-aside">
      <WorkbenchShortcuts />
      <section class="wb-panel wb-reminder">
        <h3>规则提醒</h3>
        <p v-if="data">
          当前检索范围有
          <b>{{ data.priority_count }}</b>
          条预占过期事项。办理前请核对预占与可用库存。
        </p>
        <p v-else>待办读取后显示现有规则的提醒。</p>
        <button
          v-if="data?.priority_count"
          class="wb-link"
          @click="
            priority = true;
            kind = 'all';
            offset = 0;
          "
        >
          查看优先核对事项 ↗
        </button>
        <p class="wb-note">依据现有业务规则汇总，无 AI 推断。</p>
      </section>
      <WorkbenchDrafts ref="draftsPanel" :active="active" />
    </aside>
  </div>
  <div class="wb-foot">
    <span
      >{{
        data ? "待办快照截至 " + localTime(data.as_of) : "等待业务待办数据"
      }}
      · 点击查看原单后保留筛选条件。</span
    >
  </div>
</template>
