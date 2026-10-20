<script setup lang="ts">
import DocumentFilters from "./DocumentFilters.vue";
import {
  emptyDocumentSearch,
  documentQuery,
  type DocumentSearch,
} from "./documentSearch";
const filters = ref(emptyDocumentSearch());
function applyFilters(value: DocumentSearch) {
  filters.value = value;
  void load(true);
}
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, quantity, useCommand, type PageData } from "./business";
import {
  transferStates,
  type TransferDocument,
  type TransferOptions,
} from "./transfers";
import TransferDetail from "./TransferDetail.vue";
interface Candidate {
  id: string;
  name: string;
  sku_code: string;
  lot_no: string;
  location_code: string;
  available: string;
  base_unit: string;
  qty: string;
}
const props = defineProps<{
  initialId?: string;
  initialEntityId?: string;
  initialLossId?: string;
}>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(props.initialEntityId || activeEntity.value),
  selected = ref(props.initialId || ""),
  creating = ref(false);
const documents = ref<PageData<TransferDocument>>({
  items: [],
  has_more: false,
});
const candidates = ref<PageData<Candidate>>({ items: [], has_more: false });
const options = ref<TransferOptions>({ warehouses: [], locations: [] });
const source = ref(""),
  dest = ref(""),
  location = ref(""),
  search = ref(""),
  reason = ref("");
const chosen = ref<Candidate[]>([]),
  offset = ref(0),
  sourceOffset = ref(0),
  loading = ref(false);
const warehouseDialog = ref(false),
  warehouseCode = ref(""),
  warehouseName = ref(""),
  locationCode = ref("");
const destinations = computed(() =>
  options.value.warehouses.filter((w) => w.id !== source.value),
);
const locations = computed(() =>
  options.value.locations.filter((l) => l.warehouse_id === dest.value),
);
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  operation = "",
  warehouseOperation = "";
onUnmounted(() => epoch++);
function changeDestination() {
  location.value = locations.value[0]?.id || "";
}
const listError = ref(false);
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    selected.value = "";
    creating.value = false;
    chosen.value = [];
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const [list, opts] = await Promise.all([
      request<PageData<TransferDocument>>(
        `/api/v1/transfers?${documentQuery(entity.value, offset.value, filters.value)}`,
      ),
      request<TransferOptions>(
        `/api/v1/transfers/options?entity_id=${entity.value}`,
      ),
    ]);
    if (current === epoch) {
      documents.value = list;
      listError.value = false;
      options.value = opts;
    }
  } catch (e) {
    if (current === epoch) {
      listError.value = true;
      emit("failure", e);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function sources(reset = false) {
  if (reset) sourceOffset.value = 0;
  if (!source.value) {
    candidates.value = { items: [], has_more: false };
    return;
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const data = await request<PageData<Candidate>>(
      `/api/v1/transfers/candidates?entity_id=${entity.value}&warehouse_id=${source.value}&search=${encodeURIComponent(search.value)}&offset=${sourceOffset.value}`,
    );
    if (current === epoch) candidates.value = data;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function changeSource() {
  chosen.value = [];
  if (!destinations.value.some((w) => w.id === dest.value))
    dest.value = destinations.value[0]?.id || "";
  changeDestination();
  sources(true);
}
function begin() {
  creating.value = true;
  reason.value = "";
  search.value = "";
  operation = crypto.randomUUID();
  source.value = options.value.warehouses[0]?.id || "";
  changeSource();
}
function toggle(row: Candidate, checked: boolean) {
  chosen.value = chosen.value.filter((r) => r.id !== row.id);
  if (checked) chosen.value.push({ ...row, qty: "" });
}
async function create() {
  await execute<{ id: string }>(
    "/api/v1/transfers",
    {
      client_operation_id: operation,
      entity_id: entity.value,
      source_warehouse_id: source.value,
      destination_warehouse_id: dest.value,
      destination_location_id: location.value,
      reason: reason.value,
      lines: chosen.value.map((r) => ({ balance_id: r.id, qty: r.qty })),
    },
    (result) => {
      creating.value = false;
      selected.value = result.id;
    },
  );
}
function beginWarehouse() {
  warehouseCode.value = "";
  warehouseName.value = "";
  locationCode.value = "";
  warehouseOperation = crypto.randomUUID();
  warehouseDialog.value = true;
}
async function saveWarehouse() {
  await execute(
    "/api/v1/warehouses",
    {
      client_operation_id: warehouseOperation,
      entity_id: entity.value,
      code: warehouseCode.value,
      name: warehouseName.value,
      location_code: locationCode.value,
    },
    async () => {
      warehouseDialog.value = false;
      await load();
    },
  );
}
onMounted(() => {
  if (!props.initialId) load();
});
onUnmounted(
  registerCatalogChanges(
    () => ({
      dirty: creating.value || warehouseDialog.value,
      busy: busy.value,
    }),
    "transfers",
  ),
);
</script>
<template>
  <TransferDetail
    v-if="selected"
    :key="selected"
    :id="selected"
    :initial-loss-id="
      selected === props.initialId ? props.initialLossId : undefined
    "
    @failure="emit('failure', $event)"
    @back="load(true)"
  />
  <section v-else class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button v-if="!creating" @click="load()">刷新调拨</el-button>
      <el-button
        v-if="!creating && can('warehouses.write')"
        @click="beginWarehouse"
        >新增实体仓库</el-button
      >
      <el-button
        v-if="!creating && can('transfers.write')"
        type="primary"
        :disabled="options.warehouses.length < 2"
        @click="begin"
        >新建调拨</el-button
      >
      <el-button v-if="creating" :disabled="busy" @click="load(true)"
        >返回调拨列表</el-button
      >
    </div>
    <template v-if="!creating">
      <p class="form-note">
        同一主体的两个实体仓库之间调拨。独立审核后发出，收货可分多次登记；未收到的数量保留在途。调拨不改变企业批次成本总额。
      </p>
      <p v-if="options.warehouses.length < 2" class="form-note">
        请先由管理员新增目的仓库和首个库位，再建立调拨申请。
      </p>
      <DocumentFilters
        :applied="filters"
        :states="transferStates"
        placeholder="单号、调出 / 调入仓库或说明"
        :disabled="loading || busy"
        advanced-state
        @apply="applyFilters"
      />
      <p v-if="listError" role="alert" class="form-note">
        查询失败，当前保留上次成功结果；请重试查询。
      </p>
      <el-table :data="documents.items" empty-text="暂无调拨单">
        <el-table-column prop="number" label="调拨单号" min-width="255" />
        <el-table-column label="调出 → 调入" min-width="200"
          ><template #default="{ row }"
            >{{ row.source_name }} → {{ row.destination_name }}</template
          ></el-table-column
        >
        <el-table-column label="状态" width="130"
          ><template #default="{ row }">{{
            transferStates[row.state]
          }}</template></el-table-column
        >
        <el-table-column prop="reason" label="调拨说明" min-width="180" />
        <el-table-column label="操作" width="110"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="selected = row.id"
              >调拨详情</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <div class="pager">
        <span>每页最多 50 条</span
        ><el-button
          :disabled="!offset || loading"
          @click="
            offset -= 50;
            load();
          "
          >上一页</el-button
        ><el-button
          :disabled="!documents.has_more || loading"
          @click="
            offset += 50;
            load();
          "
          >下一页</el-button
        >
      </div>
    </template>
    <form v-else class="business-form" @submit.prevent="create">
      <h3>建立调拨申请</h3>
      <div class="form-grid">
        <label
          >调出仓库<select
            v-model="source"
            :disabled="busy"
            required
            @change="changeSource"
          >
            <option v-for="w in options.warehouses" :key="w.id" :value="w.id">
              {{ w.name }}
            </option>
          </select></label
        >
        <label
          >调入仓库<select
            v-model="dest"
            :disabled="busy"
            required
            @change="changeDestination"
          >
            <option v-for="w in destinations" :key="w.id" :value="w.id">
              {{ w.name }}
            </option>
          </select></label
        >
        <label
          >目的库位<select v-model="location" :disabled="busy" required>
            <option v-for="l in locations" :key="l.id" :value="l.id">
              {{ l.code }}{{ l.name !== l.code ? " · " + l.name : "" }}
            </option>
          </select></label
        >
      </div>
      <p class="form-note">
        按来源库位和批次选择，最多 50
        行，数量使用基本单位。申请和审核不预占库存，发出时重新校验实际可用数量及质量。
      </p>
      <div class="business-toolbar">
        <label
          >搜索商品或批号<input
            v-model="search"
            maxlength="100"
            @keydown.enter.prevent="sources(true)" /></label
        ><el-button :disabled="busy" @click="sources(true)">查询批次</el-button>
      </div>
      <el-table :data="candidates.items" empty-text="没有匹配的库存批次">
        <el-table-column label="选择" width="70"
          ><template #default="{ row }"
            ><input
              type="checkbox"
              :checked="chosen.some((r) => r.id === row.id)"
              :aria-label="
                '选择 ' + row.name + ' ' + row.lot_no + ' ' + row.location_code
              "
              :disabled="
                busy ||
                (!chosen.some((r) => r.id === row.id) && chosen.length >= 50)
              "
              @change="
                toggle(row, ($event.target as HTMLInputElement).checked)
              " /></template
        ></el-table-column>
        <el-table-column prop="name" label="商品" min-width="180" />
        <el-table-column prop="lot_no" label="批号" min-width="140" />
        <el-table-column prop="location_code" label="来源库位" width="110" />
        <el-table-column label="当前未占用数量" width="150"
          ><template #default="{ row }"
            >{{ quantity(row.available) }} {{ row.base_unit }}</template
          ></el-table-column
        >
      </el-table>
      <div class="pager">
        <span>已选 {{ chosen.length }} 行</span
        ><el-button
          :disabled="!sourceOffset || busy || loading"
          @click="
            sourceOffset -= 50;
            sources();
          "
          >上一页</el-button
        ><el-button
          :disabled="!candidates.has_more || busy || loading"
          @click="
            sourceOffset += 50;
            sources();
          "
          >下一页</el-button
        >
      </div>
      <div v-for="(r, i) in chosen" :key="r.id" class="form-grid">
        <p>
          {{ i + 1 }}. {{ r.name }} · {{ r.lot_no }} · {{ r.location_code }}
        </p>
        <label
          >调拨数量（{{ r.base_unit }}）<input
            v-model="r.qty"
            :aria-label="'调拨数量 ' + (i + 1)"
            required
            inputmode="decimal"
            maxlength="22"
            :disabled="busy"
        /></label>
      </div>
      <label
        >调拨说明<input
          v-model="reason"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <el-button
        native-type="submit"
        type="primary"
        :loading="busy"
        :disabled="!chosen.length || loading"
        >提交调拨申请</el-button
      >
    </form>
  </section>
  <el-dialog
    v-model="warehouseDialog"
    title="新增实体仓库"
    width="560px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
  >
    <form class="business-form" @submit.prevent="saveWarehouse">
      <p class="form-note">
        在当前经营主体下新增实体仓库，并建立首个库位。可用于采购、销售和调拨。
      </p>
      <label
        >仓库编码<input
          v-model="warehouseCode"
          required
          maxlength="50"
          :disabled="busy"
      /></label>
      <label
        >仓库名称<input
          v-model="warehouseName"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <label
        >首个库位编码<input
          v-model="locationCode"
          required
          maxlength="50"
          :disabled="busy"
      /></label>
      <el-button native-type="submit" type="primary" :loading="busy"
        >保存仓库</el-button
      >
    </form>
  </el-dialog>
</template>
