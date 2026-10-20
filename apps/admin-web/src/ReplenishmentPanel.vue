<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import PartyLookup from "./PartyLookup.vue";
import { can, labels, positive, quantity, useCommand } from "./business";
import { localTime } from "./sales";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{
  failure: [error: unknown];
  purchase: [source: { id: string; entity_id: string; sale_id: string }];
}>();
interface Item {
  sku_id: string;
  name: string;
  code: string;
  base_unit: string;
  qty_scale: number;
  remaining_qty: string;
  available_qty: string;
  shortage_qty: string;
  pending_purchase_qty: string;
  suggested_qty: string;
  sale_eligible: boolean;
}
interface Data {
  order: { id: string; entity_id: string; version: string; state: string };
  items: Item[];
  purchases: {
    id: string;
    number: string;
    supplier_name: string;
    warehouse_name: string;
    approval_state: string;
    fulfillment_state: string;
  }[];
  data_as_of: string;
}
const data = ref<Data | null>(null),
  loading = ref(false),
  failed = ref(false);
const supplier = ref("");
const form = ref<
  (Item & { qty: string; unit_price: string; tax_rate: string })[]
>([]);
const { busy, execute } = useCommand((e) => emit("failure", e));
const canCreate = computed(
  () => can("purchase.write") && data.value?.order.state === "APPROVED",
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  const prior = new Map(form.value.map((row) => [row.sku_id, row]));
  loading.value = true;
  failed.value = false;
  data.value = null;
  form.value = [];
  try {
    const result = await request<Data>(
      `/api/v1/sales-orders/${props.id}/replenishment`,
    );
    if (current !== epoch) return;
    data.value = result;
    form.value = result.items.map((row) => ({
      ...row,
      qty: row.suggested_qty,
      unit_price: prior.get(row.sku_id)?.unit_price || "",
      tax_rate: prior.get(row.sku_id)?.tax_rate || "0.13",
    }));
  } catch (e) {
    if (current === epoch) {
      failed.value = true;
      emit("failure", e);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function openPurchase(id: string) {
  if (data.value)
    emit("purchase", {
      id,
      entity_id: data.value.order.entity_id,
      sale_id: props.id,
    });
}
async function save() {
  if (!data.value) return;
  await execute<{ id: string }>(
    `/api/v1/sales-orders/${props.id}/replenishment`,
    {
      expected_version: data.value.order.version,
      supplier_id: supplier.value,
      lines: form.value
        .filter((row) => positive(row.qty))
        .map((row) => ({
          sku_id: row.sku_id,
          qty: row.qty,
          unit_price: row.unit_price,
          tax_rate: row.tax_rate,
        })),
    },
    (result) => openPurchase(result.id),
  );
}
onMounted(load);
</script>
<template>
  <section class="panel" aria-label="销售缺货与补货" v-loading="loading">
    <div class="panel-heading">
      <h3>缺货与补货</h3>
      <el-button :disabled="busy || loading" @click="load"
        >刷新补货建议</el-button
      >
    </div>
    <p class="form-note">
      按原销售仓库及最低剩余效期核对，数量和进价均按商品基本单位填写。建议补货＝未分配需求－当前可售－本单已关联未入库采购量，最低为0。
    </p>
    <el-alert
      v-if="failed"
      title="补货信息未能加载，请刷新重试。"
      type="warning"
      :closable="false"
    />
    <template v-if="data">
      <p class="form-note">
        查询时间：{{
          localTime(data.data_as_of)
        }}。已关联未入库量包含待审核、未到货及已到货未入库的非拒收数量；不含其他采购单，请先核对已有采购。库存仍由其他订单共享，入库后需要回原销售单分配。
      </p>
      <el-table :data="data.items" empty-text="没有销售商品">
        <el-table-column label="商品 / 基本单位" min-width="200"
          ><template #default="{ row }"
            ><b>{{ row.name }}</b
            ><small class="cell-sub">{{ row.code }} / {{ row.base_unit }}</small
            ><small v-if="!row.sale_eligible" class="cell-sub"
              >商品暂不符合销售条件，请先核查资料或冻结状态</small
            ></template
          ></el-table-column
        >
        <el-table-column
          v-for="col in [
            { field: 'remaining_qty', label: '未分配需求' },
            { field: 'available_qty', label: '当前可售' },
            { field: 'shortage_qty', label: '当前缺口' },
            { field: 'pending_purchase_qty', label: '已关联未入库' },
            { field: 'suggested_qty', label: '建议补货' },
          ]"
          :key="col.field"
          :label="col.label"
          min-width="115"
          ><template #default="{ row }">{{
            quantity(row[col.field])
          }}</template></el-table-column
        >
      </el-table>
      <form
        v-if="canCreate && data.items.some((r) => positive(r.suggested_qty))"
        class="business-form"
        @submit.prevent="save"
      >
        <h4>生成补货采购单</h4>
        <label
          >补货供应商<PartyLookup
            v-model="supplier"
            :entity="data.order.entity_id"
            channel="purchase"
            label="补货供应商"
            required
            :disabled="busy"
        /></label>
        <div v-for="(row, index) in form" :key="row.sku_id" class="line-editor">
          <b>{{ row.name }} · {{ row.base_unit }}</b>
          <div class="form-grid three">
            <label
              >本次补货数量 {{ index + 1
              }}<input
                v-model="row.qty"
                required
                :disabled="busy || !positive(row.suggested_qty)"
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,6})?"
                maxlength="21"
            /></label>
            <label
              >基本单位含税进价 {{ index + 1
              }}<input
                v-model="row.unit_price"
                :required="positive(row.qty)"
                :disabled="busy || !positive(row.qty)"
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,6})?"
                maxlength="21"
            /></label>
            <label
              >采购税率 {{ index + 1
              }}<input
                v-model="row.tax_rate"
                :required="positive(row.qty)"
                :disabled="busy || !positive(row.qty)"
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,6})?"
                maxlength="8"
            /></label>
          </div>
        </div>
        <p class="form-note">
          可减少本次数量，暂不采购的商品填0。刷新会重新填入建议数量并保留进价、税率。保存时重新核对需求；生成后继续独立审核、到货、验收和入库，不会自动发货。
        </p>
        <div class="form-actions">
          <el-button
            native-type="submit"
            type="primary"
            :disabled="busy || loading || !form.some((r) => positive(r.qty))"
            >保存补货采购单</el-button
          >
        </div>
      </form>
      <p v-else class="form-note">
        {{
          data.order.state !== "APPROVED"
            ? "当前销售单不能生成补货采购；已有采购仍保留，销售关闭或修改不会自动取消采购。"
            : !can("purchase.write")
              ? "当前账号可查看补货信息，生成采购单需要采购制单权限。"
              : "当前没有建议新增的补货数量，可继续处理已关联采购或回原单分配发货。"
        }}
      </p>
      <h4>关联补货采购</h4>
      <el-table :data="data.purchases" empty-text="尚未生成关联补货采购单">
        <el-table-column prop="number" label="采购单号" min-width="300" />
        <el-table-column prop="supplier_name" label="供应商" min-width="150" />
        <el-table-column
          prop="warehouse_name"
          label="收货仓库"
          min-width="130"
        />
        <el-table-column label="采购状态" min-width="160"
          ><template #default="{ row }"
            >{{ labels[row.approval_state] }} /
            {{ labels[row.fulfillment_state] }}</template
          ></el-table-column
        >
        <el-table-column label="操作" width="120"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="openPurchase(row.id)"
              >办理补货采购</el-button
            ></template
          ></el-table-column
        >
      </el-table>
    </template>
  </section>
</template>
