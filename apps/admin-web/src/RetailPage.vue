<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import SaleStockPanel from "./SaleStockPanel.vue";
import { onMounted, onUnmounted, ref } from "vue";
import { request, session, type Named } from "./api";
import { can, useCommand, type PageData } from "./business";
import { localTime } from "./sales";
import { retailStates, type RetailSale } from "./retail";
import PackagingSelect from "./PackagingSelect.vue";
import RetailDetail from "./RetailDetail.vue";
import DocumentFilters from "./DocumentFilters.vue";
import {
  documentQuery,
  emptyDocumentSearch,
  type DocumentSearch,
} from "./documentSearch";
const props = defineProps<{ initialEntityId?: string; initialId?: string }>();
const emit = defineEmits<{
  failure: [error: unknown];
  returnSale: [source: { posting_id: string; entity_id: string }];
}>();
interface Product {
  id: string;
  name: string;
  code: string;
  base_unit: string;
  specification: string;
}
const entity = ref(props.initialEntityId || activeEntity.value),
  selected = ref(props.initialId || "");
const documents = ref<PageData<RetailSale>>({ items: [], has_more: false });
const filters = ref(emptyDocumentSearch());
function applyFilters(value: DocumentSearch) {
  filters.value = value;
  void load(true);
}
function changeEntity() {
  filters.value = emptyDocumentSearch();
  void load(true);
}
const offset = ref(0),
  loading = ref(false),
  creating = ref(false),
  search = ref("");
const products = ref<PageData<Product>>({ items: [], has_more: false }),
  productOffset = ref(0);
const warehouse = ref(""),
  warehouses = ref<Named[]>([]),
  operation = ref("");
const cart = ref<
  (Product & {
    qty: string;
    packaging_id: string;
    price: string;
    tax: string;
  })[]
>([]);
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  productEpoch = 0;
onUnmounted(() => {
  epoch++;
  productEpoch++;
});
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    selected.value = "";
    documents.value = { items: [], has_more: false };
  }
  const current = ++epoch;
  loading.value = true;
  documents.value = { items: [], has_more: false };
  try {
    const data = await request<PageData<RetailSale>>(
      `/api/v1/retail/sales?${documentQuery(entity.value, offset.value, filters.value)}`,
    );
    if (current === epoch) documents.value = data;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function find(reset = true) {
  if (reset) productOffset.value = 0;
  const current = ++productEpoch;
  try {
    const data = await request<PageData<Product>>(
      `/api/v1/retail/catalog?${new URLSearchParams({ entity_id: entity.value, search: search.value, offset: String(productOffset.value) })}`,
    );
    if (current === productEpoch) products.value = data;
  } catch (e) {
    if (current === productEpoch) emit("failure", e);
  }
}
async function begin() {
  creating.value = true;
  cart.value = [];
  search.value = "";
  warehouse.value = "";
  warehouses.value = [];
  operation.value = crypto.randomUUID();
  const current = epoch;
  try {
    const options = await request<{ warehouses: Named[] }>(
      `/api/v1/retail/options?entity_id=${entity.value}`,
    );
    if (current !== epoch) return;
    warehouses.value = options.warehouses;
    warehouse.value = options.warehouses[0]?.id || "";
    await find();
  } catch (e) {
    if (current === epoch) emit("failure", e);
  }
}
function add(row: Product) {
  if (!cart.value.some((r) => r.id === row.id) && cart.value.length < 50)
    cart.value.push({
      ...row,
      packaging_id: "",
      qty: "1",
      price: "",
      tax: "0.13",
    });
}
async function create() {
  await execute<{ id: string }>(
    "/api/v1/retail/sales",
    {
      client_operation_id: operation.value,
      entity_id: entity.value,
      warehouse_id: warehouse.value,
      lines: cart.value.map((r) => ({
        sku_id: r.id,
        packaging_id: r.packaging_id || undefined,
        qty: r.qty,
        unit_price: r.price,
        tax_rate: r.tax,
      })),
    },
    (data) => {
      creating.value = false;
      selected.value = data.id;
    },
  );
}
onMounted(() => load());
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: creating.value, busy: busy.value }),
    "retail",
  ),
);
</script>

<template>
  <RetailDetail
    v-if="selected"
    :key="selected"
    :id="selected"
    @return-sale="emit('returnSale', $event)"
    @back="
      selected = '';
      load();
    "
    @failure="emit('failure', $event)"
  />
  <section v-else class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button @click="load()">刷新零售列表</el-button>
      <el-button v-if="can('retail.sell')" type="primary" @click="begin"
        >新建零售单</el-button
      >
    </div>
    <DocumentFilters
      :key="entity"
      :applied="filters"
      :disabled="loading"
      :states="retailStates"
      placeholder="零售单号"
      @apply="applyFilters"
    />
    <p class="form-note">
      现金零售按批次预占、收款后交付。可售商品为已审核非处方药与普通医疗器械；账户可在“往来结算”中建立。
    </p>
    <el-table
      :data="documents.items"
      empty-text="没有符合条件的零售单，可调整条件或清除筛选"
    >
      <el-table-column prop="number" label="零售单号" min-width="230" />
      <el-table-column label="状态" width="160"
        ><template #default="{ row }"
          ><el-tag
            :type="row.state === 'PAID_PENDING_POST' ? 'warning' : 'info'"
            >{{
              row.auto_released_at ? "已超时取消" : retailStates[row.state]
            }}</el-tag
          ></template
        ></el-table-column
      >
      <el-table-column prop="warehouse_name" label="出货仓库" min-width="140" />
      <el-table-column prop="gross_amount" label="应收（元）" width="130" />
      <el-table-column label="创建时间" min-width="170"
        ><template #default="{ row }">{{
          localTime(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="100"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="selected = row.id"
            >零售详情</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <div class="pager">
      <span>每页 50 条</span
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
  </section>
  <el-dialog
    v-model="creating"
    title="新建零售单"
    class="business-dialog"
    width="960px"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
  >
    <form class="business-form" @submit.prevent="create">
      <fieldset :disabled="busy">
        <div class="form-grid">
          <label
            >出货仓库<select v-model="warehouse" required>
              <option value="" disabled>请选择</option>
              <option v-for="w in warehouses" :key="w.id" :value="w.id">
                {{ w.name }}
              </option>
            </select></label
          >
          <label
            >搜索商品<input
              v-model="search"
              placeholder="商品名称或 SKU 编码"
              maxlength="100"
              @keydown.enter.prevent="find()"
          /></label>
        </div>
        <el-button @click="find()">查找可售商品</el-button>
        <el-table :data="products.items" height="200">
          <el-table-column
            prop="name"
            label="商品"
            min-width="170"
          /><el-table-column
            prop="specification"
            label="规格"
          /><el-table-column prop="code" label="SKU 编码" />
          <el-table-column width="90"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                :disabled="
                  cart.some((r) => r.id === row.id) || cart.length >= 50
                "
                @click="add(row)"
                >加入</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <div class="pager">
          <el-button
            :disabled="!productOffset"
            @click="
              productOffset -= 50;
              find(false);
            "
            >上一批商品</el-button
          ><el-button
            :disabled="!products.has_more"
            @click="
              productOffset += 50;
              find(false);
            "
            >下一批商品</el-button
          >
        </div>
        <div v-for="(row, index) in cart" :key="row.id" class="retail-cart-row">
          <strong
            >{{ row.name
            }}<small
              >{{ row.specification }} · {{ row.base_unit }}</small
            ></strong
          >
          <PackagingSelect
            v-model="row.packaging_id"
            :sku="row.id"
            :disabled="busy"
            :label="'零售单位 ' + (index + 1)"
            @failure="emit('failure', $event)"
          />
          <label
            >数量<input
              v-model="row.qty"
              :aria-label="'零售数量 ' + (index + 1)"
              inputmode="decimal"
              required
          /></label>
          <label
            >含税单价（元）<input
              v-model="row.price"
              :aria-label="'零售单价 ' + (index + 1)"
              inputmode="decimal"
              required
          /></label>
          <label
            >税率<input
              v-model="row.tax"
              :aria-label="'零售税率 ' + (index + 1)"
              inputmode="decimal"
              required
          /></label>
          <el-button @click="cart.splice(index, 1)">移除</el-button>
        </div>
        <SaleStockPanel
          v-if="creating"
          channel="retail"
          :entity="entity"
          :warehouse="warehouse"
          :skus="cart.map((row) => row.id)"
          :disabled="busy"
        />
        <p class="form-note">
          核对售价与税率后建单；系统根据库存批次分配数量，并计算应收金额。建单成功后预占
          15 分钟，请及时完成收款。
        </p>
        <div class="form-actions">
          <el-button @click="creating = false">返回</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="busy"
            :disabled="!cart.length || !warehouse"
            >建单并预占库存</el-button
          >
        </div>
      </fieldset>
    </form>
  </el-dialog>
</template>

<style scoped>
.retail-cart-row {
  display: grid;
  grid-template-columns: minmax(160px, 2fr) repeat(4, minmax(90px, 1fr)) auto;
  gap: 14px;
  align-items: end;
  padding: 16px 0;
  border-bottom: 1px solid #e3e9e8;
}
.retail-cart-row small {
  display: block;
  font-weight: normal;
  margin-top: 8px;
  color: #607672;
}
@media (max-width: 700px) {
  .retail-cart-row {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
