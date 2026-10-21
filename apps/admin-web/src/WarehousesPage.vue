<script setup lang="ts">
import MasterDataLayout from "./MasterDataLayout.vue";
import MasterCoreActions from "./MasterCoreActions.vue";
import { copyMasterCode, extensionTools, type MasterTool } from "./masterTools";
import { ElMessageBox } from "element-plus";
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, useCommand, type PageData } from "./business";

interface Warehouse {
  id: string;
  code: string;
  name: string;
  version: string;
  location_count: number;
}
interface Location {
  id: string;
  code: string;
  name: string;
  version: string;
}
interface Detail {
  warehouse: Warehouse;
  locations: Location[];
  has_more: boolean;
  history: {
    id: string;
    created_at: string;
    created_by: string;
    action: string;
    detail: {
      code?: string;
      name?: string;
      location_code?: string;
      before?: string;
      after?: string;
      reason?: string;
    };
  }[];
  history_has_more: boolean;
}
defineProps<{ active?: boolean }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(activeEntity.value);
const search = ref(""),
  locationSearch = ref(""),
  offset = ref(0),
  locationOffset = ref(0),
  historyOffset = ref(0);
const data = ref<PageData<Warehouse>>({ items: [], has_more: false });
const detail = ref<Detail | null>(null),
  loading = ref(false);
const mode = ref<"" | "create" | "location" | "rename" | "rename-location">("");
const code = ref(""),
  name = ref(""),
  firstLocation = ref(""),
  reason = ref("");
const selectedLocation = ref<Location | null>(null);
const historyOpen = ref(false),
  formBaseline = ref("");
const formSignature = () =>
  JSON.stringify([code.value, name.value, firstLocation.value, reason.value]);
const title = computed(
  () =>
    ({
      create: "新增实体仓库",
      location: "新增库位",
      rename: "修改仓库名称",
      "rename-location": "修改库位名称",
      "": "",
    })[mode.value],
);
const actions: Record<string, string> = {
  "inventory.warehouse_created": "新建仓库",
  "inventory.location_created": "新增库位",
  "inventory.warehouse_renamed": "修改仓库名称",
  "inventory.location_renamed": "修改库位名称",
};
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  operation = "";
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (reset) offset.value = 0;
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<PageData<Warehouse>>(
      `/api/v1/warehouses?entity_id=${entity.value}&search=${encodeURIComponent(search.value)}&offset=${offset.value}`,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function open(id: string, reset = false) {
  if (reset) {
    locationSearch.value = "";
    locationOffset.value = 0;
    historyOffset.value = 0;
    selectedLocation.value = null;
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<Detail>(
      `/api/v1/warehouses/${id}?search=${encodeURIComponent(locationSearch.value)}&offset=${locationOffset.value}&history_offset=${historyOffset.value}`,
    );
    if (current === epoch) detail.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin(value: typeof mode.value, location: Location | null = null) {
  mode.value = value;
  selectedLocation.value = location;
  name.value =
    value === "rename" ? detail.value!.warehouse.name : location?.name || "";
  code.value = "";
  firstLocation.value = "";
  reason.value = "";
  operation = crypto.randomUUID();
  formBaseline.value = formSignature();
}
async function save() {
  const id = detail.value?.warehouse.id;
  let path = "/api/v1/warehouses";
  let body: unknown;
  if (mode.value === "create") {
    body = {
      client_operation_id: operation,
      entity_id: entity.value,
      code: code.value,
      name: name.value,
      location_code: firstLocation.value,
    };
  } else if (mode.value === "location") {
    path += `/${id}/locations`;
    body = {
      client_operation_id: operation,
      expected_version: detail.value!.warehouse.version,
      code: code.value,
      name: name.value,
      reason: reason.value,
    };
  } else {
    path += `/${id}${mode.value === "rename-location" ? `/locations/${selectedLocation.value!.id}` : ""}/rename`;
    body = {
      expected_version:
        selectedLocation.value?.version || detail.value!.warehouse.version,
      name: name.value,
      reason: reason.value,
    };
  }
  const creating = mode.value === "create";
  await execute<{ id: string }>(path, body, async (result) => {
    mode.value = "";
    await load();
    await open(creating ? result.id : id!, true);
  });
}
function back() {
  detail.value = null;
  mode.value = "";
  load();
}
onMounted(() => load());
onUnmounted(
  registerCatalogChanges(
    () => ({
      dirty: !!mode.value && formSignature() !== formBaseline.value,
      busy: busy.value,
    }),
    "warehouses",
  ),
);

const tools = computed<MasterTool[]>(() => {
  const wait =
    busy.value || loading.value
      ? "正在处理，请稍候"
      : mode.value
        ? "请先结束当前任务"
        : "";
  const need = wait || (!detail.value ? "请先打开一个仓库" : "");
  const write = can("warehouses.write") ? "" : "无维护权限";
  return [
    {
      key: "revise",
      label: "修改仓库名称",
      short: "修订",
      icon: "✎",
      group: "资料维护",
      disabled: write || need,
    },
    {
      key: "refresh",
      label: "刷新资料",
      icon: "↻",
      group: "资料维护",
      disabled: wait,
    },
    {
      key: "new",
      label: "新增实体仓库",
      short: "新建",
      icon: "＋",
      group: "资料维护",
      disabled: write || wait,
    },
    {
      key: "location",
      label: "新增库位",
      icon: "＋",
      group: "资料维护",
      disabled: write || need,
    },
    {
      key: "rename-location",
      label: "修改库位名称",
      icon: "✎",
      group: "资料维护",
      disabled:
        write ||
        need ||
        (!selectedLocation.value ? "请在中间选择一个库位" : ""),
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
      key: "copyCode",
      label: "复制仓库编码",
      icon: "⧉",
      group: "辅助操作",
      disabled: need,
    },
  ];
});
async function closeForm(done: () => void) {
  if (busy.value) return;
  if (formSignature() !== formBaseline.value) {
    try {
      await ElMessageBox.confirm(
        "放弃尚未保存的仓库 / 库位修改？",
        "取消维护",
        { confirmButtonText: "放弃修改", cancelButtonText: "继续编辑" },
      );
    } catch {
      return;
    }
  }
  mode.value = "";
  done();
}
async function toolAction(key: string) {
  if (tools.value.find((t) => t.key === key)?.disabled) return;
  if (key === "revise") begin("rename");
  if (key === "new") begin("create");
  if (key === "location") begin("location");
  if (key === "rename-location")
    begin("rename-location", selectedLocation.value);
  if (key === "history") historyOpen.value = true;
  if (key === "copyCode") await copyMasterCode(detail.value?.warehouse.code);
  if (key === "refresh") {
    if (detail.value) await open(detail.value.warehouse.id);
    else await load();
  }
}
</script>

<template>
  <div class="warehouse-workspace">
    <MasterDataLayout
      :active="active"
      label="仓库与库位"
      :tools="extensionTools(tools)"
      @action="toolAction"
    >
      <aside class="data-scope">
        <h2>仓库与库位</h2>
        <nav aria-label="仓库范围">
          <button
            :class="{ active: !detail }"
            :disabled="loading || busy || !!mode"
            @click="back"
          >
            全部仓库</button
          ><button
            v-for="w in data.items"
            :key="w.id"
            :class="{ active: detail?.warehouse.id === w.id }"
            :disabled="loading || busy || !!mode"
            @click="open(w.id, true)"
          >
            {{ w.name }}<small class="cell-sub">{{ w.code }}</small>
          </button>
        </nav>
        <p>本次查询范围内的仓库；更多仓库可通过列表查询、翻页。</p>
      </aside>
      <section class="panel" v-loading="loading">
        <template v-if="!detail">
          <header class="master-toolbar">
            <h2>仓库与库位</h2>
            <MasterCoreActions
              :tools="tools"
              :keys="['new']"
              @action="toolAction"
            />
          </header>
          <form class="business-toolbar" @submit.prevent="load(true)">
            <label
              >仓库编码或名称<input
                v-model="search"
                maxlength="100"
                :disabled="busy || !!mode"
            /></label>
            <el-button
              native-type="submit"
              :disabled="loading || busy || !!mode"
              >查询仓库</el-button
            >
          </form>
          <el-table :data="data.items" row-key="id">
            <el-table-column prop="code" label="仓库编码" min-width="150" />
            <el-table-column prop="name" label="仓库名称" min-width="220" />
            <el-table-column prop="location_count" label="库位数" width="100" />
            <el-table-column label="操作" width="140"
              ><template #default="{ row }"
                ><el-button
                  link
                  type="primary"
                  :disabled="loading || busy"
                  @click="open(row.id, true)"
                  >管理库位</el-button
                ></template
              ></el-table-column
            >
          </el-table>
          <div class="pager">
            <span>每页最多 50 个实体仓库</span
            ><el-button
              :disabled="!offset || loading"
              @click="
                offset -= 50;
                load();
              "
              >上一页</el-button
            ><el-button
              :disabled="!data.has_more || loading"
              @click="
                offset += 50;
                load();
              "
              >下一页</el-button
            >
          </div>
        </template>
        <template v-else>
          <div class="business-toolbar">
            <el-button :disabled="busy || loading" @click="back"
              >返回仓库列表</el-button
            >
            <MasterCoreActions
              :tools="tools"
              :keys="['revise', 'location']"
              @action="toolAction"
            />
          </div>
          <h3>{{ detail.warehouse.name }}</h3>
          <p class="form-note">
            仓库编码：{{ detail.warehouse.code }} · 版本
            {{
              detail.warehouse.version
            }}。库位编码在本仓库内唯一；修改名称不改变库位归属及库存。
          </p>
          <form
            class="business-toolbar"
            @submit.prevent="
              locationOffset = 0;
              open(detail.warehouse.id);
            "
          >
            <label
              >库位编码或名称<input
                v-model="locationSearch"
                maxlength="100"
                :disabled="busy || loading"
            /></label>
            <el-button native-type="submit" :disabled="busy || loading"
              >查询库位</el-button
            >
          </form>
          <el-table :data="detail.locations" row-key="id">
            <el-table-column prop="code" label="库位编码" min-width="160" />
            <el-table-column prop="name" label="库位名称" min-width="220" />
            <el-table-column prop="version" label="版本" width="80" />
            <el-table-column label="选择库位" width="180"
              ><template #default="{ row }"
                ><el-button
                  link
                  :aria-pressed="selectedLocation?.id === row.id"
                  @click="selectedLocation = row"
                  >{{
                    selectedLocation?.id === row.id ? "已选择" : "选择库位"
                  }}</el-button
                ><el-button
                  link
                  type="primary"
                  :disabled="
                    !can('warehouses.write') || busy || loading || !!mode
                  "
                  @click="begin('rename-location', row)"
                  >修改名称</el-button
                ></template
              ></el-table-column
            >
          </el-table>
          <div class="pager">
            <span>库位每页最多 50 条</span
            ><el-button
              :disabled="!locationOffset || loading || busy"
              @click="
                locationOffset -= 50;
                open(detail.warehouse.id);
              "
              >上一页库位</el-button
            ><el-button
              :disabled="!detail.has_more || loading || busy"
              @click="
                locationOffset += 50;
                open(detail.warehouse.id);
              "
              >下一页库位</el-button
            >
          </div>
        </template>
      </section>
      <el-drawer
        v-model="historyOpen"
        title="变更记录"
        size="min(760px,96vw)"
        append-to-body
        ><template v-if="detail"
          ><p class="data-task-heading">
            {{ detail.warehouse.name }} · {{ detail.warehouse.code }}
          </p>
          <h3>维护记录</h3>
          <el-table :data="detail.history" row-key="id">
            <el-table-column label="时间 / 操作人" min-width="230"
              ><template #default="{ row }"
                >{{ new Date(row.created_at).toLocaleString()
                }}<small class="cell-sub">{{ row.created_by }}</small></template
              ></el-table-column
            >
            <el-table-column label="操作" width="145"
              ><template #default="{ row }">{{
                actions[row.action] || row.action
              }}</template></el-table-column
            >
            <el-table-column label="内容" min-width="240"
              ><template #default="{ row }"
                >{{ row.detail.code }} ·
                <template v-if="row.detail.before !== undefined"
                  >{{ row.detail.before }} → {{ row.detail.after }}</template
                ><template v-else
                  >{{ row.detail.name
                  }}<span v-if="row.detail.location_code">
                    · 首库位 {{ row.detail.location_code }}</span
                  ></template
                ></template
              ></el-table-column
            >
            <el-table-column label="原因" min-width="170"
              ><template #default="{ row }">{{
                row.detail.reason || "初始建档"
              }}</template></el-table-column
            >
          </el-table>
          <div class="pager">
            <span>记录每页最多 50 条</span
            ><el-button
              :disabled="!historyOffset || loading || busy"
              @click="
                historyOffset -= 50;
                open(detail.warehouse.id);
              "
              >上一页记录</el-button
            ><el-button
              :disabled="!detail.history_has_more || loading || busy"
              @click="
                historyOffset += 50;
                open(detail.warehouse.id);
              "
              >下一页记录</el-button
            >
          </div>
        </template></el-drawer
      >
    </MasterDataLayout>
    <el-dialog
      :model-value="!!mode"
      :title="title"
      width="560px"
      class="business-dialog"
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
      :before-close="closeForm"
    >
      <form class="business-form" @submit.prevent="save">
        <p class="form-note">
          {{
            mode === "create"
              ? "在当前经营主体新建实体仓库及首个库位，保存后即可用于采购入库和调拨。"
              : mode === "location"
                ? "在当前仓库建立新的收货库位，新增库位初始无库存。"
                : "仅修改显示名称，编码、归属与已有业务记录保持不变。"
          }}
        </p>
        <label v-if="mode === 'create' || mode === 'location'"
          >{{ mode === "create" ? "仓库编码" : "库位编码"
          }}<input v-model="code" required maxlength="50" :disabled="busy"
        /></label>
        <label
          >{{ mode === "create" || mode === "rename" ? "仓库名称" : "库位名称"
          }}<input v-model="name" required maxlength="200" :disabled="busy"
        /></label>
        <label v-if="mode === 'create'"
          >首个库位编码<input
            v-model="firstLocation"
            required
            maxlength="50"
            :disabled="busy"
        /></label>
        <label v-else
          >维护原因<input
            v-model="reason"
            required
            maxlength="200"
            :disabled="busy"
        /></label>
        <el-button :disabled="busy" @click="closeForm(() => {})"
          >取消</el-button
        >
        <el-button native-type="submit" type="primary" :loading="busy"
          >保存{{
            mode === "create" ? "仓库" : mode === "location" ? "库位" : "名称"
          }}</el-button
        >
      </form>
    </el-dialog>
  </div>
</template>
