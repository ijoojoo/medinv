<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { can, useCommand } from "./business";
import { showReference } from "./workspace";
import { registerCatalogChanges } from "./catalogChanges";
import type { WorkQueueItem } from "./workQueue";
const props = defineProps<{ active?: boolean }>();
const emit = defineEmits<{
  failure: [error: unknown];
  open: [item: WorkQueueItem];
}>();
type Message = {
  message_id: string;
  source: string;
  source_key: string;
  source_revision: string;
  title: string;
  description?: string;
  due: string | null;
  page: string;
  target_id: string;
  tab?: string;
  read?: boolean;
  work_item?: WorkQueueItem;
  channel?: string;
  scope_codes?: string[];
  enforcement?: string;
  impact?: string;
  case_id: string | null;
  case_version: string;
  process_state: string;
  condition_present: boolean;
  condition_changed: boolean;
  can_manage: boolean;
  claimed_by: string | null;
  claimed_by_me: boolean;
  assigned_staff_id: string | null;
  assigned_name: string;
  due_on: string | null;
  closed_at: string | null;
};
type History = {
  created_at: string;
  action: string;
  detail: { reason: string };
};
const source = ref("work"),
  view = ref("current"),
  mine = ref(false),
  offset = ref(0),
  previous = ref<number[]>([]);
const loading = ref(false),
  failed = ref(false),
  data = ref<{
    items: Message[];
    has_more: boolean;
    disabled: boolean;
    next_offset: number;
  }>({ items: [], has_more: false, disabled: false, next_offset: 0 });
const selected = ref<Message | null>(null),
  history = ref<History[]>([]),
  dialog = ref(false),
  checking = ref(false);
const dialogVisible = computed({
  get: () => dialog.value && props.active !== false,
  set: (value) => {
    dialog.value = value;
  },
});
const action = ref("CLAIM"),
  reason = ref(""),
  staffId = ref(""),
  dueOn = ref(""),
  staffLoading = ref(false);
const staff = ref<{ id: string; code: string; name: string; job: string }[]>(
    [],
  ),
  staffMore = ref(false);
const { busy, execute } = useCommand((e) => emit("failure", e));
const categories = computed(() =>
  [
    { id: "work", name: "业务待办", visible: true },
    { id: "certificate", name: "资质到期", visible: can("catalog.read") },
    { id: "scope", name: "经营范围异常", visible: can("catalog.read") },
    {
      id: "catalog_review",
      name: "主体资料待审",
      visible: can("catalog.read"),
    },
    { id: "maintenance", name: "养护提醒", visible: can("quality.read") },
  ].filter((c) => c.visible),
);
const stateNames: Record<string, string> = {
  NEW: "待处理",
  PROCESSING: "处理中",
  RESOLVED: "已消除 · 待确认关闭",
  CLOSED: "已关闭",
  REOPEN_REQUIRED: "再次触发 · 待重新认领",
};
const actionNames: Record<string, string> = {
  CLAIM: "认领处理",
  ASSIGN: "分派负责人",
  NOTE: "补充处理记录",
  CLOSE: "核查并关闭",
};
const dateText = (value: string | null) =>
  value?.replace("T", " ").slice(0, 16) || "—";
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dialog.value && !!reason.value.trim(), busy: busy.value }),
    "messages",
  ),
);
let epoch = 0,
  detailEpoch = 0,
  staffEpoch = 0;
onUnmounted(() => {
  epoch++;
  detailEpoch++;
  staffEpoch++;
});
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    previous.value = [];
  }
  const token = ++epoch;
  loading.value = true;
  failed.value = false;
  try {
    const params = new URLSearchParams({
      entity_id: activeEntity.value,
      source: source.value,
      offset: String(offset.value),
    });
    if (view.value !== "current") {
      params.set("state", view.value === "closed" ? "CLOSED" : "PROCESSING");
      params.set("mine", String(mine.value));
    }
    const result = await request<typeof data.value>(
      `/api/v1/messages${view.value === "current" ? "" : "/cases"}?${params}`,
    );
    if (token === epoch) data.value = result;
  } catch (e) {
    if (token === epoch) {
      failed.value = true;
      emit("failure", e);
    }
  } finally {
    if (token === epoch) loading.value = false;
  }
}
async function mark(row: Message) {
  await execute(
    "/api/v1/messages/read",
    {
      entity_id: activeEntity.value,
      source: row.source,
      message_id: row.message_id,
    },
    () => {
      row.read = true;
    },
  );
}
async function open(row: Message) {
  dialog.value = false;
  if (row.work_item) emit("open", row.work_item);
  else await showReference(row.page, row.target_id, row.tab);
}
async function inspect(row: Message) {
  const token = ++detailEpoch;
  selected.value = row;
  history.value = [];
  reason.value = "";
  staffId.value = row.assigned_staff_id || "";
  dueOn.value = row.due_on || "";
  action.value =
    row.process_state === "NEW" || row.process_state === "REOPEN_REQUIRED"
      ? "CLAIM"
      : row.condition_present
        ? "NOTE"
        : "CLOSE";
  dialog.value = true;
  if (!row.case_id) return;
  checking.value = true;
  try {
    const result = await request<{ item: Message; history: History[] }>(
      `/api/v1/messages/cases/${row.case_id}?entity_id=${activeEntity.value}`,
    );
    if (token === detailEpoch) {
      selected.value = result.item;
      history.value = result.history;
    }
  } catch (e) {
    if (token === detailEpoch) emit("failure", e);
  } finally {
    if (token === detailEpoch) checking.value = false;
  }
}
async function searchStaff(search = "") {
  const token = ++staffEpoch;
  staffLoading.value = true;
  try {
    const result = await request<{
      items: typeof staff.value;
      has_more: boolean;
    }>(
      `/api/v1/messages/responsible-staff?entity_id=${activeEntity.value}&search=${encodeURIComponent(search)}`,
    );
    if (token === staffEpoch) {
      staff.value = result.items;
      staffMore.value = result.has_more;
    }
  } catch (e) {
    if (token === staffEpoch) emit("failure", e);
  } finally {
    if (token === staffEpoch) staffLoading.value = false;
  }
}
watch(action, (value) => {
  if (value === "ASSIGN") void searchStaff();
});
async function submit() {
  if (!selected.value || !reason.value.trim()) return;
  const row = selected.value;
  await execute<Message>(
    "/api/v1/messages/actions",
    {
      entity_id: activeEntity.value,
      source: row.source,
      source_key: row.source_key,
      expected_version: row.case_version,
      observed_revision: row.source_revision,
      action: action.value,
      assigned_staff_id:
        action.value === "ASSIGN" ? staffId.value || null : null,
      due_on: action.value === "ASSIGN" ? dueOn.value || null : null,
      reason: reason.value,
    },
    async (result) => {
      await inspect(result);
      await load();
    },
  );
}
watch(
  () => props.active,
  (value) => {
    if (value && !dialog.value) void load();
  },
);
onMounted(() => load());
</script>
<template>
  <section
    class="panel messages-page"
    aria-label="消息和提醒"
    v-loading="loading"
  >
    <header class="master-toolbar">
      <div>
        <h2>消息和提醒</h2>
        <small>原资料实时核查 · 团队协同处理 · 已读状态仅属于当前账号</small>
      </div>
      <el-button @click="load()">刷新提醒</el-button>
    </header>
    <nav class="master-tabs" aria-label="提醒分类">
      <button
        v-for="category in categories"
        :key="category.id"
        :class="{ active: source === category.id }"
        @click="
          source = category.id;
          load(true);
        "
      >
        {{ category.name }}
      </button>
    </nav>
    <div class="followup-toolbar">
      <div class="view-options" aria-label="处理状态">
        <button
          v-for="option in [
            { id: 'current', name: '当前提醒' },
            { id: 'processing', name: '处理中' },
            { id: 'closed', name: '已关闭' },
          ]"
          :key="option.id"
          :aria-pressed="view === option.id"
          :class="{ active: view === option.id }"
          @click="
            view = option.id;
            load(true);
          "
        >
          {{ option.name }}
        </button>
      </div>
      <label v-if="view !== 'current'" class="mine-filter"
        ><input
          v-model="mine"
          type="checkbox"
          @change="load(true)"
        />仅我认领</label
      >
      <span class="scope-note" v-if="source === 'scope'"
        >按已审核经营范围及当前规则核查；提醒本身不改变交易准入。</span
      >
    </div>
    <p v-if="failed" role="alert">读取失败，请刷新重试。</p>
    <p v-else-if="data.disabled" class="master-empty-tab">
      当前主体已关闭此类站内提醒，可在企业设置中开启。已有处理事项仍可在“处理中”查询。
    </p>
    <div v-else>
      <article
        v-for="row in data.items"
        :key="row.source_key"
        class="message-row"
        :class="{ unread: view === 'current' && !row.read }"
      >
        <span v-if="view === 'current'" class="message-dot">{{
          row.read ? "○" : "●"
        }}</span>
        <div class="message-main">
          <div class="message-title">
            <strong>{{ row.title }}</strong
            ><span
              class="status-badge"
              :class="row.process_state.toLowerCase()"
              >{{ stateNames[row.process_state] }}</span
            >
          </div>
          <p v-if="row.description" class="issue-description">
            {{ row.description }}
          </p>
          <p v-if="row.channel">
            {{ row.channel === "PURCHASE" ? "采购供货" : "销售购货" }} ·
            覆盖分类 {{ row.scope_codes?.join("、") || "按药品范围名称" }} ·
            当前准入{{
              row.enforcement === "BLOCK"
                ? "拦截"
                : row.enforcement === "WARN"
                  ? "提示"
                  : "关闭控制"
            }}
            · {{ row.impact === "PARTY" ? "按单位控制" : "按品类控制" }}
          </p>
          <p>
            <span v-if="row.due"
              >{{
                ["work", "catalog_review"].includes(row.source)
                  ? "业务时间"
                  : "到期日期"
              }}：{{ dateText(row.due) }} · </span
            ><span v-if="view === 'current'"
              >{{ row.read ? "已读" : "未读" }} · </span
            >{{
              row.claimed_by_me
                ? "本人认领"
                : row.claimed_by
                  ? "已有成员认领"
                  : "尚未认领"
            }}<span v-if="row.assigned_name">
              · 负责人 {{ row.assigned_name }}</span
            ><span v-if="row.due_on"> · 计划完成 {{ row.due_on }}</span>
          </p>
          <p v-if="row.condition_changed" class="changed-note">
            原资料或触发原因已有变化，请重新核对。
          </p>
        </div>
        <div class="message-actions">
          <el-button
            v-if="view === 'current' && !row.read"
            :disabled="busy"
            @click="mark(row)"
            >标记已读</el-button
          ><el-button
            v-if="row.can_manage || row.case_id"
            :disabled="busy"
            @click="inspect(row)"
            >{{ row.can_manage ? "处理事项" : "处理记录" }}</el-button
          ><el-button type="primary" plain @click="open(row)">{{
            row.source === "work" ? "进入原单" : "查看资料"
          }}</el-button>
        </div>
      </article>
      <p v-if="!data.items.length" class="master-empty-tab">
        {{
          source === "scope" && data.has_more
            ? "本批范围未发现异常，可继续核查下一批"
            : "当前分类没有提醒事项"
        }}
      </p>
      <div class="master-pagination">
        <el-button
          :disabled="!previous.length || loading"
          @click="
            offset = previous.pop() || 0;
            load();
          "
          >上一页</el-button
        ><span
          >{{ previous.length + 1 }}
          {{ source === "scope" && view === "current" ? "批" : "页" }}</span
        ><el-button
          :disabled="!data.has_more || loading"
          @click="
            previous.push(offset);
            offset = data.next_offset;
            load();
          "
          >下一页</el-button
        >
      </div>
    </div>
  </section>
  <el-dialog
    v-model="dialogVisible"
    title="提醒处理"
    width="min(740px, 95vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    :before-close="
      (done: () => void) => {
        if (!busy) done();
      }
    "
  >
    <div v-if="selected" class="case-detail" v-loading="checking">
      <h3>{{ selected.title }}</h3>
      <p>{{ selected.description }}</p>
      <div
        class="case-condition"
        :class="{ resolved: !selected.condition_present }"
      >
        <strong>{{ stateNames[selected.process_state] }}</strong>
        <p>
          {{
            selected.condition_present
              ? "原事项仍触发提醒。请进入原资料或原单办理，再核查关闭。"
              : "当前规则下原事项已不再触发提醒，可填写处理说明后确认关闭。"
          }}
        </p>
        <p v-if="selected.condition_changed">
          原资料或触发原因已有变化，以下操作将按当前情况记录。
        </p>
      </div>
      <div class="case-links">
        <el-button @click="open(selected)">{{
          selected.source === "work" ? "进入原单" : "查看资料"
        }}</el-button
        ><el-button :disabled="busy || checking" @click="inspect(selected)"
          >重新核查</el-button
        >
      </div>
      <form
        v-if="selected.can_manage && selected.process_state !== 'CLOSED'"
        class="business-form"
        @submit.prevent="submit"
      >
        <label
          >处理动作<select v-model="action" :disabled="busy">
            <option
              v-if="
                !selected.claimed_by ||
                selected.claimed_by_me ||
                selected.process_state === 'REOPEN_REQUIRED'
              "
              value="CLAIM"
            >
              {{
                selected.process_state === "REOPEN_REQUIRED"
                  ? "重新认领"
                  : "认领处理"
              }}
            </option>
            <template v-if="selected.process_state !== 'REOPEN_REQUIRED'"
              ><option value="ASSIGN">分派负责人</option>
              <option value="NOTE">补充处理记录</option>
              <option
                v-if="selected.case_id"
                value="CLOSE"
                :disabled="selected.condition_present"
              >
                核查并关闭
              </option></template
            >
          </select></label
        >
        <template v-if="action === 'ASSIGN'"
          ><label
            >负责人（在职职员）<el-select
              v-model="staffId"
              filterable
              remote
              :remote-method="searchStaff"
              :loading="staffLoading"
              placeholder="搜索职员姓名或编码"
              :disabled="busy"
              ><el-option
                v-if="
                  selected.assigned_staff_id &&
                  !staff.some((s) => s.id === selected!.assigned_staff_id)
                "
                :value="selected.assigned_staff_id"
                :label="selected.assigned_name" /><el-option
                v-for="person in staff"
                :key="person.id"
                :value="person.id"
                :label="`${person.code} · ${person.name}${person.job ? ' · ' + person.job : ''}`" /></el-select></label
          ><small v-if="staffMore"
            >仅显示前50名，请输入姓名或编码缩小范围。</small
          ><label
            >计划完成日期<input
              v-model="dueOn"
              type="date"
              :disabled="busy" /></label
          ><small
            >职员负责人用于跟进责任；“仅我认领”按当前登录账号筛选。</small
          ></template
        >
        <label
          >处理说明<textarea
            v-model="reason"
            maxlength="200"
            required
            rows="3"
            :disabled="busy"
            placeholder="记录已联系、资料补充、分派原因或核查结论"
          />
        </label>
        <el-button
          type="primary"
          native-type="submit"
          :loading="busy"
          :disabled="
            checking ||
            !reason.trim() ||
            (action === 'ASSIGN' && !staffId) ||
            (action === 'CLOSE' && selected.condition_present)
          "
          >{{ actionNames[action] }}</el-button
        >
      </form>
      <section class="case-history" aria-label="处理记录">
        <h4>处理记录</h4>
        <p v-if="!history.length">尚无处理记录</p>
        <article v-for="(event, index) in history" :key="index">
          <strong>{{
            actionNames[event.action.replace("reminder.", "").toUpperCase()] ||
            event.action
          }}</strong
          ><time>{{ dateText(event.created_at) }}</time>
          <p>{{ event.detail.reason }}</p>
        </article>
        <small v-if="history.length === 50"
          >显示最近50条记录，完整历史保留在审计记录中。</small
        >
      </section>
    </div>
  </el-dialog>
</template>
<style scoped>
.messages-page {
  max-width: 1440px;
  margin: auto;
}
.master-toolbar small {
  display: block;
  margin-top: 8px;
  color: var(--ui-muted);
}
.followup-toolbar {
  display: flex;
  gap: 18px;
  align-items: center;
  flex-wrap: wrap;
  padding: 16px 0;
}
.view-options {
  display: flex;
  border: 1px solid var(--ui-border);
  border-radius: 7px;
  padding: 3px;
  background: var(--ui-tint);
}
.view-options button {
  border: 0;
  padding: 7px 16px;
  background: transparent;
  color: var(--ui-muted);
  cursor: pointer;
  border-radius: 4px;
}
.view-options button.active {
  background: white;
  color: var(--ui-accent);
  box-shadow: 0 1px 3px #0001;
}
.mine-filter,
.scope-note {
  font-size: 12px;
  color: var(--ui-muted);
}
.message-row {
  display: flex;
  gap: 14px;
  align-items: center;
  padding: 18px 12px;
  border-bottom: 1px solid var(--ui-border);
}
.message-main {
  flex: 1;
  min-width: 0;
}
.message-title {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.message-row strong {
  font-size: 14px;
  overflow-wrap: anywhere;
}
.message-row p {
  font-size: 12px;
  color: var(--ui-muted);
  margin: 7px 0;
  line-height: 1.7;
}
.message-row .issue-description {
  color: var(--ui-text);
}
.message-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.message-actions .el-button + .el-button {
  margin-left: 0;
}
.message-dot {
  color: var(--ui-muted);
}
.unread .message-dot {
  color: var(--ui-accent);
}
.unread {
  background: var(--ui-tint);
}
.status-badge {
  padding: 3px 7px;
  font-size: 11px;
  border-radius: 4px;
  background: #edf3ff;
  color: #2859ab;
}
.status-badge.resolved,
.status-badge.closed {
  background: #eaf6ed;
  color: #28613b;
}
.status-badge.reopen_required {
  background: #fff2df;
  color: #885b16;
}
.case-condition {
  background: var(--ui-tint);
  padding: 14px;
  border-radius: 7px;
  margin: 15px 0;
}
.case-condition.resolved {
  background: #eaf6ed;
}
.case-condition p {
  font-size: 13px;
  margin: 8px 0 0;
  line-height: 1.7;
}
.case-links {
  margin-bottom: 16px;
}
.case-detail .business-form {
  display: grid;
  gap: 14px;
}
.case-detail small {
  color: var(--ui-muted);
}
.case-history {
  border-top: 1px solid var(--ui-border);
  margin-top: 24px;
  padding-top: 12px;
}
.case-history article {
  padding: 12px 0;
  border-bottom: 1px solid var(--ui-border);
}
.case-history time {
  float: right;
  color: var(--ui-muted);
  font-size: 12px;
}
.case-history p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 8px 0;
}
@media (max-width: 900px) {
  .message-row {
    flex-wrap: wrap;
  }
  .message-main {
    min-width: 80%;
  }
  .message-actions {
    width: 100%;
  }
}
</style>
