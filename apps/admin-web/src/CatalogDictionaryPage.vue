<script setup lang="ts">
import MasterDataLayout from "./MasterDataLayout.vue";
import { copyMasterCode, type MasterTool } from "./masterTools";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessageBox, ElMessage } from "element-plus";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import { localTime } from "./sales";
import { checkDictionary, type CatalogDictionary } from "./catalogDictionaries";
const props = defineProps<{
  active?: boolean;
  initialKey?: string;
  embedded?: boolean;
}>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
  saved: [];
}>();
type Item = CatalogDictionary["items"][number];
type Group = { key: string; name: string; icon: string; description: string };
const root = ref<HTMLElement>();
const editMode = ref(!!props.embedded),
  historyOpen = ref(false);
const groups = ref<Group[]>([]),
  definitions = ref<CatalogDictionary[]>([]);
const selected = ref<CatalogDictionary | null>(null),
  group = ref("product"),
  loading = ref(false),
  failed = ref(false);
const groupSearch = ref(""),
  search = ref(""),
  status = ref("all"),
  reason = ref("");
const form = ref({
  mode: "SUGGEST" as "SUGGEST" | "STRICT",
  items: [] as Item[],
});
const baseline = ref("");
const drawer = ref(false),
  editIndex = ref(-1),
  draft = ref<Item>({ value: "", active: true, order: 10 }),
  draftBaseline = ref("");
const drawerDirty = computed(
  () => drawer.value && JSON.stringify(draft.value) !== draftBaseline.value,
);
const dirty = computed(
  () =>
    !!selected.value &&
    (JSON.stringify(form.value) !== baseline.value ||
      !!reason.value ||
      drawerDirty.value),
);
const writable = computed(() => can("catalog.configure"));
const kindNames = {
  SYSTEM: "系统标准",
  HYBRID: "系统与企业扩展",
  TENANT: "企业可维护",
  TREE: "企业分级字典",
};
const { busy, execute } = useCommand((e) => emit("failure", e));
const checks = computed(() =>
  (selected.value?.bindings || []).map((b) => ({
    ...b,
    ...checkDictionary(form.value, b),
  })),
);
const blocked = computed(() => checks.value.some((c) => c.blocked));
const defaultEnabled = computed(() =>
  [
    "purchase_approval",
    "inspection_qualified",
    "inspection_mixed",
    "inspection_rejected",
  ].includes(selected.value?.key || ""),
);
const defaultValue = computed({
  get: () => form.value.items.find((i) => i.is_default)?.id || "",
  set: (id: string) => {
    form.value.items.forEach((i) => (i.is_default = i.id === id));
  },
});
const fixed = computed(
  () =>
    !["dosage_form", "trade_name", "storage_conditions", "base_unit"].includes(
      selected.value?.key || "",
    ),
);
const currentGroup = computed(() =>
  groups.value.find((g) => g.key === group.value),
);
const tabs = computed(() =>
  definitions.value.filter((d) => d.group_key === group.value),
);
const visibleGroups = computed(() =>
  groups.value.filter((g) =>
    (
      g.name +
      definitions.value
        .filter((d) => d.group_key === g.key)
        .map((d) => d.name)
        .join(" ")
    ).includes(groupSearch.value.trim()),
  ),
);
const remembered = new Map<string, string>();
const collapsed = ref(new Set<string>()),
  highlighted = ref("");
const rows = computed(() => {
  const result: {
    item: Item;
    index: number;
    depth: number;
    children: boolean;
  }[] = [];
  function walk(parent: string | null, depth: number) {
    form.value.items
      .map((item, index) => ({ item, index }))
      .filter((r) => (r.item.parent_id || null) === parent)
      .sort(
        (a, b) =>
          a.item.order - b.item.order ||
          a.item.value.localeCompare(b.item.value),
      )
      .forEach((r) => {
        const matches = [r.item.value, r.item.code, r.item.description]
          .join(" ")
          .toLocaleLowerCase()
          .includes(search.value.toLocaleLowerCase());
        const children = form.value.items.some(
          (i) => i.parent_id === r.item.id && !!i.parent_id,
        );
        if (
          matches &&
          (status.value === "all" ||
            r.item.active === (status.value === "active"))
        )
          result.push({ ...r, depth, children });
        if (
          r.item.id &&
          (search.value || !collapsed.value.has(r.item.id)) &&
          depth < 5
        )
          walk(r.item.id, depth + 1);
      });
  }
  walk(null, 0);
  return result;
});
watch(
  () => dirty.value || busy.value || loading.value,
  (v) => emit("editing", v),
  { immediate: true },
);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    "dictionaries",
  ),
);
let epoch = 0,
  searchEpoch = 0;
onUnmounted(() => {
  epoch++;
  searchEpoch++;
  document.removeEventListener("keydown", shortcut);
});
function reset() {
  if (!selected.value) return;
  form.value = {
    mode: selected.value.mode,
    items: selected.value.items.map((i) => ({ ...i })),
  };
  baseline.value = JSON.stringify(form.value);
  reason.value = "";
  drawer.value = false;
}
async function discard() {
  if (busy.value || loading.value) return false;
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm("当前字典有未保存修改，是否放弃？", "切换字典", {
      confirmButtonText: "放弃修改",
      cancelButtonText: "继续编辑",
      closeOnClickModal: false,
    });
    reset();
    return true;
  } catch {
    return false;
  }
}
async function load(key?: string) {
  const current = ++epoch;
  loading.value = true;
  failed.value = false;
  try {
    const nav = await request<{ groups: Group[]; items: CatalogDictionary[] }>(
      "/api/v1/catalog/dictionary-navigation",
    );
    const detail = await request<CatalogDictionary>(
      `/api/v1/catalog/dictionaries/${key || nav.items.find((d) => d.group_key === "product")?.key || nav.items[0].key}`,
    );
    if (current !== epoch) return;
    groups.value = nav.groups;
    definitions.value = nav.items;
    selected.value = detail;
    editMode.value = !!props.embedded;
    group.value = detail.group_key || "product";
    remembered.set(group.value, detail.key);
    reset();
  } catch (e) {
    if (current === epoch) {
      failed.value = true;
      emit("failure", e);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function choose(key: string) {
  if (key === selected.value?.key || !(await discard())) return;
  search.value = "";
  status.value = "all";
  highlighted.value = "";
  collapsed.value = new Set();
  await load(key);
}
async function chooseGroup(key: string) {
  const d =
    remembered.get(key) ||
    definitions.value.find((d) => d.group_key === key)?.key;
  if (d) await choose(d);
  else if (await discard()) {
    group.value = key;
    selected.value = null;
  }
}
function edit(index: number) {
  editIndex.value = index;
  const id = crypto.randomUUID();
  draft.value =
    index >= 0
      ? { ...form.value.items[index] }
      : {
          id,
          code: "V_" + id.replaceAll("-", "").toUpperCase(),
          value: "",
          description: "",
          parent_id: null,
          active: true,
          order: Math.min(
            9999,
            Math.max(0, ...form.value.items.map((i) => i.order)) + 10,
          ),
          origin: "TENANT",
        };
  draftBaseline.value = JSON.stringify(draft.value);
  drawer.value = true;
}
async function closeDrawer(done: () => void) {
  if (drawerDirty.value) {
    try {
      await ElMessageBox.confirm("尚未应用当前字典值，是否放弃？", "关闭编辑", {
        confirmButtonText: "放弃",
        cancelButtonText: "继续编辑",
      });
    } catch {
      return;
    }
  }
  done();
}
function applyItem() {
  if (!draft.value.value.trim()) return;
  if (
    form.value.items.some(
      (i, n) =>
        n !== editIndex.value &&
        (i.value === draft.value.value.trim() || i.code === draft.value.code),
    )
  ) {
    ElMessage.warning("名称或编码重复");
    return;
  }
  const item = { ...draft.value, value: draft.value.value.trim() };
  if (editIndex.value < 0) form.value.items.push(item);
  else form.value.items[editIndex.value] = item;
  drawer.value = false;
}
function move(index: number, delta: number) {
  const item = form.value.items[index];
  const siblings = form.value.items
    .filter((i) => (i.parent_id || null) === (item.parent_id || null))
    .sort((a, b) => a.order - b.order);
  const at = siblings.indexOf(item),
    to = at + delta;
  if (to < 0 || to >= siblings.length) return;
  [siblings[at], siblings[to]] = [siblings[to], siblings[at]];
  siblings.forEach((i, n) => (i.order = (n + 1) * 10));
}
function toggleTree(id: string) {
  const next = new Set(collapsed.value);
  next.has(id) ? next.delete(id) : next.add(id);
  collapsed.value = next;
}
async function save() {
  if (
    !editMode.value ||
    !selected.value ||
    blocked.value ||
    !writable.value ||
    drawer.value
  )
    return;
  const key = selected.value.key;
  await execute(
    `/api/v1/catalog/dictionaries/${key}`,
    {
      expected_version: selected.value.version,
      ...form.value,
      reason: reason.value,
    },
    async () => {
      await load(key);
      document.dispatchEvent(
        new CustomEvent("catalog-dictionary-saved", { detail: key }),
      );
      emit("saved");
    },
  );
}
const globalOpen = ref(false),
  globalQuery = ref(""),
  globalLoading = ref(false);
const results = ref<{ key: string; path: string; value: string; id: string }[]>(
  [],
);
async function globalSearch() {
  const current = ++searchEpoch;
  globalLoading.value = true;
  try {
    const data = await request<{ items: typeof results.value }>(
      `/api/v1/catalog/dictionary-search?search=${encodeURIComponent(globalQuery.value)}`,
    );
    if (current === searchEpoch) results.value = data.items;
  } catch (e) {
    if (current === searchEpoch) emit("failure", e);
  } finally {
    if (current === searchEpoch) globalLoading.value = false;
  }
}
async function locate(row: (typeof results.value)[number]) {
  if (!(await discard())) return;
  await load(row.key);
  status.value = "all";
  search.value = row.value;
  highlighted.value = row.id;
  globalOpen.value = false;
}
function shortcut(e: KeyboardEvent) {
  if (
    (e.ctrlKey || e.metaKey) &&
    e.key.toLowerCase() === "k" &&
    root.value?.getClientRects().length &&
    !props.embedded
  ) {
    e.preventDefault();
    e.stopPropagation();
    globalOpen.value = true;
  }
}
onMounted(() => {
  void load(props.initialKey);
  document.addEventListener("keydown", shortcut);
});

const tools = computed<MasterTool[]>(() => {
  const wait = busy.value || loading.value ? "正在处理，请稍候" : "";
  const task = wait || (editMode.value ? "请先结束修订任务" : "");
  const need = task || (!selected.value ? "请先选择一个字典" : "");
  return [
    {
      key: "revise",
      label: "修订字典",
      short: "修订",
      icon: "✎",
      group: "资料维护",
      disabled: !writable.value ? "无配置权限" : need,
    },
    {
      key: "refresh",
      label: "刷新资料",
      icon: "↻",
      group: "资料维护",
      disabled: task,
    },
    {
      key: "history",
      label: "变更记录",
      short: "记录",
      icon: "◷",
      group: "关联查阅",
      disabled: need,
    },
    {
      key: "search",
      label: "搜索全部字典 / 字典值",
      short: "搜索",
      icon: "⌕",
      group: "辅助操作",
      disabled: wait,
    },
    {
      key: "copyCode",
      label: "复制字典编码",
      icon: "⧉",
      group: "辅助操作",
      disabled: !selected.value ? "请先选择一个字典" : "",
    },
  ];
});
async function finishEdit() {
  if (await discard()) editMode.value = false;
}
async function toolAction(key: string) {
  if (tools.value.find((t) => t.key === key)?.disabled) return;
  if (key === "revise") editMode.value = true;
  if (key === "refresh") await load(selected.value?.key);
  if (key === "history") historyOpen.value = true;
  if (key === "search") globalOpen.value = true;
  if (key === "copyCode") await copyMasterCode(selected.value?.key);
}
</script>
<template>
  <section ref="root" class="dictionary-workspace" v-loading="loading">
    <p v-if="failed" role="alert">
      字典读取失败，当前修改已保留。<el-button
        @click="!dirty && load(selected?.key)"
        :disabled="dirty"
        >重试读取</el-button
      >
    </p>
    <MasterDataLayout
      :active="active"
      :disabled="embedded"
      label="数据字典"
      :tools="tools"
      @action="toolAction"
    >
      <aside v-if="!embedded" class="dictionary-sidebar">
        <div class="dictionary-sidehead">
          一级大分类 <span>{{ groups.length }}</span>
        </div>
        <input
          v-model="groupSearch"
          placeholder="搜索分类或字典"
          aria-label="搜索分类或字典"
        />
        <nav aria-label="字典大分类">
          <button
            v-for="g in visibleGroups"
            :key="g.key"
            :class="{ active: group === g.key }"
            @click="chooseGroup(g.key)"
          >
            <i>{{ g.icon }}</i
            ><span>{{ g.name }}</span
            ><small>{{
              definitions.filter((d) => d.group_key === g.key).length
            }}</small>
          </button>
        </nav>
        <p class="dictionary-sidebar-note">
          分类用于查找；字典值用于选项。系统状态与业务审批独立管理。
        </p>
      </aside>
      <div class="dictionary-content">
        <div v-if="!embedded" class="dictionary-content-top">
          <div class="dictionary-sectionbar">
            <h2>{{ currentGroup?.name }}</h2>
          </div>
          <nav class="dictionary-tabs" aria-label="具体字典">
            <button
              v-for="d in tabs"
              :key="d.key"
              :class="{ active: selected?.key === d.key }"
              :aria-current="selected?.key === d.key ? 'page' : undefined"
              @click="choose(d.key)"
            >
              {{ d.name }} <small>{{ d.count }}</small>
            </button>
          </nav>
        </div>
        <form v-if="selected" class="dictionary-main" @submit.prevent="save">
          <div class="dictionary-heading">
            <h3>{{ selected.name }}</h3>
            <span class="dictionary-kind" :class="selected.kind">{{
              kindNames[selected.kind || "TENANT"]
            }}</span
            ><span v-if="!can('catalog.configure')" class="form-note"
              >当前账号只读</span
            >
          </div>
          <p class="dictionary-code">
            {{ selected.key }} · v{{ selected.version }}
          </p>
          <div class="dictionary-hint">
            {{
              selected.kind === "SYSTEM"
                ? "系统标准值由系统维护，企业可查看。"
                : "已保存值保持名称和编码；停用不影响历史记录，新业务不再选用。"
            }}
          </div>
          <div class="dictionary-table-panel">
            <div class="dictionary-toolbar">
              <strong
                >字典值
                <small
                  >{{ rows.length }} / {{ form.items.length }}</small
                ></strong
              ><input
                v-model="search"
                aria-label="搜索字典值"
                placeholder="名称、编码或说明"
              />
              <div class="dictionary-status">
                <button
                  v-for="s in [
                    { key: 'all', name: '全部' },
                    { key: 'active', name: '已启用' },
                    { key: 'inactive', name: '已停用' },
                  ]"
                  :key="s.key"
                  type="button"
                  :class="{ active: status === s.key }"
                  @click="status = s.key"
                >
                  {{ s.name }}
                </button>
              </div>
              <el-button
                v-if="editMode && writable && selected.extendable"
                type="primary"
                :disabled="busy || form.items.length >= 200"
                @click="edit(-1)"
                >＋ 新增字典值</el-button
              >
            </div>
            <div class="dictionary-table-scroll">
              <table class="dictionary-table">
                <thead>
                  <tr>
                    <th v-if="editMode">排序</th>
                    <th>字典值 / 说明</th>
                    <th>稳定编码</th>
                    <th>来源</th>
                    <th>启用</th>
                    <th>当前资料引用</th>
                    <th v-if="editMode">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="r in rows"
                    :key="r.item.id || r.index"
                    :class="{ highlight: highlighted === r.item.id }"
                  >
                    <td v-if="editMode">
                      <button
                        type="button"
                        :disabled="!writable || busy"
                        :aria-label="`上移${r.item.value}`"
                        @click="move(r.index, -1)"
                      >
                        ↑</button
                      ><button
                        type="button"
                        :disabled="!writable || busy"
                        :aria-label="`下移${r.item.value}`"
                        @click="move(r.index, 1)"
                      >
                        ↓
                      </button>
                    </td>
                    <td>
                      <div
                        class="dictionary-value"
                        :style="{ paddingLeft: r.depth * 16 + 'px' }"
                      >
                        <button
                          v-if="r.children"
                          type="button"
                          :aria-label="`展开或收起${r.item.value}`"
                          @click="toggleTree(r.item.id!)"
                        >
                          {{ collapsed.has(r.item.id!) ? "▸" : "▾" }}</button
                        ><strong>{{ r.item.value }}</strong>
                      </div>
                      <small>{{ r.item.description || "—" }}</small>
                    </td>
                    <td>
                      <code :title="r.item.code">{{ r.item.code }}</code>
                    </td>
                    <td>
                      <span
                        class="dictionary-origin"
                        :class="{ tenant: r.item.origin === 'TENANT' }"
                        >{{
                          r.item.origin === "SYSTEM" ? "系统内置" : "企业扩展"
                        }}</span
                      >
                    </td>
                    <td>
                      <span v-if="!editMode">{{
                        r.item.active ? "已启用" : "已停用"
                      }}</span>
                      <el-switch
                        v-else
                        v-model="r.item.active"
                        :disabled="!writable || busy"
                        :aria-label="`${r.item.value}启用状态`"
                      /><span class="visually-hidden">{{
                        r.item.active ? "启用" : "停用"
                      }}</span>
                    </td>
                    <td :title="selected.usage?.scope">
                      {{
                        selected.usage?.supported
                          ? selected.usage.counts[r.item.value] || 0
                          : "未统计"
                      }}
                    </td>
                    <td v-if="editMode">
                      <button
                        type="button"
                        class="dictionary-link"
                        @click="edit(r.index)"
                      >
                        {{ writable ? "编辑" : "查看" }}</button
                      ><button
                        v-if="!selected.items.some((i) => i.id === r.item.id)"
                        type="button"
                        class="dictionary-link"
                        @click="form.items.splice(r.index, 1)"
                      >
                        撤回新增
                      </button>
                    </td>
                  </tr>
                  <tr v-if="!rows.length">
                    <td colspan="7" class="dictionary-empty">
                      {{
                        form.items.length
                          ? "没有符合筛选条件的字典值"
                          : "尚未配置字典值，可按企业业务需要新增。"
                      }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <footer class="dictionary-tablefoot">
              <span :class="{ unsaved: dirty }"
                >{{ dirty ? "● 修改未保存" : "● 配置已载入" }} ·
                保存后供新业务选用</span
              ><el-button v-if="dirty" :disabled="busy" @click="discard"
                >撤销未保存</el-button
              >
            </footer>
          </div>
          <details class="dictionary-rules">
            <summary>选值规则与引用范围</summary>
            <label
              >选值规则
              <select
                v-model="form.mode"
                :disabled="!editMode || fixed || !writable || busy"
              >
                <option value="SUGGEST">建议选用，可输入其他值</option>
                <option value="STRICT">只能选用启用值</option>
              </select></label
            ><label v-if="defaultEnabled"
              >默认结论
              <select
                v-model="defaultValue"
                :disabled="!editMode || !writable || busy"
              >
                <option value="">请选择一个启用值</option>
                <option
                  v-for="i in form.items.filter((i) => i.active)"
                  :key="i.id"
                  :value="i.id"
                >
                  {{ i.value }}
                </option>
              </select>
              排序不改变默认结论</label
            >
            <p>{{ selected.usage?.scope }}；已保存值保留，不提供破坏式删除。</p>
            <p
              v-for="c in checks"
              :key="c.category + ':' + c.field"
              :class="{ conflict: c.blocked }"
            >
              {{ c.category_name }} · {{ c.label }}：符合规则
              {{ c.eligible }} 项{{
                c.blocked ? "，必填规则冲突，请处理后保存" : ""
              }}
            </p>
          </details>
          <div v-if="editMode && writable" class="dictionary-savebar">
            <label
              >变更原因<input
                v-model="reason"
                maxlength="200"
                required
                placeholder="填写本次配置调整原因"
                :disabled="busy" /></label
            ><el-button
              type="primary"
              native-type="submit"
              :disabled="!dirty || blocked || drawer"
              :loading="busy"
              >保存配置</el-button
            ><el-button v-if="!embedded" :disabled="busy" @click="finishEdit"
              >取消修订</el-button
            >
          </div>
        </form>
        <div v-else class="dictionary-empty">此分类尚无可见字典</div>
      </div>
      <el-drawer
        v-model="historyOpen"
        title="变更记录"
        size="min(640px,96vw)"
        append-to-body
        ><template v-if="selected"
          ><p class="data-task-heading">
            {{ selected.name }} · {{ selected.key }}
          </p>
          <p v-if="!selected.history?.length">暂无变更记录</p>
          <p v-for="h in selected.history" :key="h.created_at">
            {{ localTime(h.created_at) }} · v{{ h.detail.version }} ·
            {{ h.detail.reason }} · {{ h.created_by }}
          </p>
        </template></el-drawer
      >
    </MasterDataLayout>
    <el-drawer
      v-model="drawer"
      :title="(editIndex < 0 ? '新增' : writable ? '编辑' : '查看') + '字典值'"
      size="min(440px, 100vw)"
      :before-close="closeDrawer"
      :close-on-click-modal="false"
      class="dictionary-drawer"
      ><p class="form-note">{{ currentGroup?.name }} / {{ selected?.name }}</p>
      <form class="business-form" @submit.prevent="applyItem">
        <fieldset :disabled="!writable || busy">
          <label
            >字典值名称<input
              v-model="draft.value"
              required
              :maxlength="selected?.capacity"
              :disabled="
                editIndex >= 0 && selected?.items.some((i) => i.id === draft.id)
              " /></label
          ><label
            >稳定编码<input
              v-model="draft.code"
              required
              maxlength="60"
              pattern="[A-Za-z0-9][A-Za-z0-9_.-]*"
              :disabled="
                editIndex >= 0 && selected?.items.some((i) => i.id === draft.id)
              " /></label
          ><label
            >说明<textarea
              v-model="draft.description"
              maxlength="300"
              rows="3"
            ></textarea></label
          ><label v-if="selected?.hierarchical"
            >上级值<select v-model="draft.parent_id">
              <option :value="null">无上级（根节点）</option>
              <option
                v-for="i in form.items.filter((i) => i.id !== draft.id)"
                :key="i.id"
                :value="i.id"
              >
                {{ i.value }}
              </option>
            </select></label
          ><label>启用状态<el-switch v-model="draft.active" /></label>
        </fieldset>
        <p class="form-note">
          应用后仍需点击“保存配置”。父子层级与引用保护由服务端统一校验。
        </p>
        <el-button v-if="writable" type="primary" native-type="submit"
          >应用到配置</el-button
        >
      </form></el-drawer
    >
    <el-dialog
      v-model="globalOpen"
      title="搜索全部字典 / 字典值"
      width="min(640px, 94vw)"
      :close-on-click-modal="false"
      ><form class="dictionary-global-search" @submit.prevent="globalSearch">
        <input
          v-model="globalQuery"
          aria-label="搜索全部字典"
          placeholder="输入名称、稳定编码或备注"
          maxlength="100"
          autofocus
        /><el-button native-type="submit" :loading="globalLoading"
          >搜索</el-button
        >
      </form>
      <div class="dictionary-search-results">
        <button v-for="(r, i) in results" :key="i" @click="locate(r)">
          <strong>{{ r.value || r.path.split(" / ").at(-1) }}</strong
          ><small>{{ r.path }}</small
          ><span>↗</span>
        </button>
        <p v-if="!results.length" class="form-note">
          输入关键词后搜索，最多显示 50 条；仅检索当前企业可见数据。
        </p>
      </div></el-dialog
    >
  </section>
</template>
<style scoped src="./dictionary.css"></style>
