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
import { onMounted, onUnmounted, ref } from "vue";
import { request, session, type Named } from "./api";
import { can, quantity, useCommand, type PageData } from "./business";
import { countStates, type CountDocument } from "./counts";
import CountDetail from "./CountDetail.vue";
interface Candidate {
  id: string;
  name: string;
  sku_code: string;
  lot_no: string;
  location_code: string;
  on_hand: string;
  base_unit: string;
}
const props = defineProps<{ initialId?: string; initialEntityId?: string }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(props.initialEntityId || activeEntity.value),
  selected = ref(props.initialId || ""),
  creating = ref(false);
const documents = ref<PageData<CountDocument>>({ items: [], has_more: false });
const candidates = ref<PageData<Candidate>>({ items: [], has_more: false });
const warehouses = ref<Named[]>([]),
  warehouse = ref(""),
  search = ref(""),
  reason = ref("");
const chosen = ref<string[]>([]),
  offset = ref(0),
  sourceOffset = ref(0),
  loading = ref(false);
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  operation = "";
onUnmounted(() => epoch++);
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
    const data = await request<PageData<CountDocument>>(
      `/api/v1/stock-counts?${documentQuery(entity.value, offset.value, filters.value)}`,
    );
    if (current === epoch) {
      documents.value = data;
      listError.value = false;
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
  if (!warehouse.value) return;
  const current = ++epoch;
  loading.value = true;
  try {
    const data = await request<PageData<Candidate>>(
      `/api/v1/stock-counts/candidates?entity_id=${entity.value}&warehouse_id=${warehouse.value}&search=${encodeURIComponent(search.value)}&offset=${sourceOffset.value}`,
    );
    if (current === epoch) candidates.value = data;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function begin() {
  creating.value = true;
  chosen.value = [];
  reason.value = "";
  search.value = "";
  operation = crypto.randomUUID();
  const current = ++epoch;
  loading.value = true;
  try {
    const options = await request<{ warehouses: Named[] }>(
      `/api/v1/stock-counts/options?entity_id=${entity.value}`,
    );
    if (current !== epoch) return;
    warehouses.value = options.warehouses;
    warehouse.value = options.warehouses[0]?.id || "";
    loading.value = false;
    await sources(true);
  } catch (e) {
    if (current === epoch) {
      loading.value = false;
      emit("failure", e);
    }
  }
}
async function create() {
  await execute<{ id: string }>(
    "/api/v1/stock-counts",
    {
      client_operation_id: operation,
      entity_id: entity.value,
      warehouse_id: warehouse.value,
      balance_ids: chosen.value,
      reason: reason.value,
    },
    async (result) => {
      creating.value = false;
      selected.value = result.id;
    },
  );
}
onMounted(() => {
  if (!selected.value) void load();
});
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: creating.value, busy: busy.value }),
    "counts",
  ),
);
</script>
<template>
  <CountDetail
    v-if="selected"
    :key="selected"
    :id="selected"
    @failure="emit('failure', $event)"
    @back="load(true)"
  />
  <section v-else class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button v-if="!creating" @click="load()">刷新盘点</el-button>
      <el-button
        v-if="!creating && can('counts.write')"
        type="primary"
        @click="begin"
        >新建盘点</el-button
      >
      <el-button v-if="creating" :disabled="busy" @click="load(true)"
        >返回盘点列表</el-button
      >
    </div>
    <template v-if="!creating">
      <p class="form-note">
        按选定批次盘点，独立审核后登记盘盈盘亏。建单后自动冻结所选主体批次的库存与成本写入（包括同批次的其他库位、仓库），过账或取消后解除；请同时暂停实物移动。
      </p>
      <DocumentFilters
        :applied="filters"
        :states="countStates"
        placeholder="单号、仓库或盘点说明"
        :disabled="loading || busy"
        advanced-state
        @apply="applyFilters"
      />
      <p v-if="listError" role="alert" class="form-note">
        查询失败，当前保留上次成功结果；请重试查询。
      </p>
      <el-table
        :data="documents.items"
        empty-text="暂无盘点单，请先新建盘点范围"
      >
        <el-table-column prop="number" label="盘点单号" min-width="260" />
        <el-table-column prop="warehouse_name" label="仓库" min-width="150" />
        <el-table-column label="状态" width="110"
          ><template #default="{ row }">{{
            countStates[row.state]
          }}</template></el-table-column
        >
        <el-table-column prop="reason" label="盘点说明" min-width="180" />
        <el-table-column label="操作" width="110"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="selected = row.id"
              >盘点详情</el-button
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
      <h3>选择盘点范围</h3>
      <div class="business-toolbar">
        <label
          >盘点仓库<select
            v-model="warehouse"
            :disabled="busy"
            @change="
              chosen = [];
              sources(true);
            "
          >
            <option v-for="w in warehouses" :key="w.id" :value="w.id">
              {{ w.name }}
            </option>
          </select></label
        >
        <label
          >搜索商品或批号<input
            v-model="search"
            maxlength="100"
            @keydown.enter.prevent="sources(true)"
        /></label>
        <el-button :disabled="busy" @click="sources(true)">查询批次</el-button>
      </div>
      <p class="form-note">
        选择已有成本记录的普通合格批次，最多 200
        个库位批次。仅所选库位数量参与差异调整；同主体同批次整体冻结，已有预占或冻结数量须先处理。建单后逐行录入实盘，不能把漏盘当作零库存。
      </p>
      <el-table
        :data="candidates.items"
        empty-text="没有匹配的可盘点批次，请先完成采购验收入库"
      >
        <el-table-column label="选择" width="70"
          ><template #default="{ row }"
            ><input
              v-model="chosen"
              type="checkbox"
              :value="row.id"
              :aria-label="
                '选择 ' + row.name + ' ' + row.lot_no + ' ' + row.location_code
              "
              :disabled="
                busy || (!chosen.includes(row.id) && chosen.length >= 200)
              " /></template
        ></el-table-column>
        <el-table-column label="商品" min-width="180"
          ><template #default="{ row }"
            >{{ row.name
            }}<small class="cell-sub">{{ row.sku_code }}</small></template
          ></el-table-column
        >
        <el-table-column prop="lot_no" label="批号" min-width="160" />
        <el-table-column prop="location_code" label="库位" width="110" />
        <el-table-column label="当前账面数量" width="150"
          ><template #default="{ row }"
            >{{ quantity(row.on_hand) }} {{ row.base_unit }}</template
          ></el-table-column
        >
      </el-table>
      <div class="pager">
        <span>已选 {{ chosen.length }} 个库位批次</span
        ><el-button
          :disabled="!sourceOffset || loading || busy"
          @click="
            sourceOffset -= 50;
            sources();
          "
          >上一页</el-button
        ><el-button
          :disabled="!candidates.has_more || loading || busy"
          @click="
            sourceOffset += 50;
            sources();
          "
          >下一页</el-button
        >
      </div>
      <label
        >盘点说明<input
          v-model="reason"
          required
          maxlength="200"
          placeholder="填写盘点原因及范围说明"
      /></label>
      <div class="business-toolbar">
        <el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="!chosen.length || loading"
          >建立盘点快照</el-button
        >
      </div>
    </form>
  </section>
</template>
