<script setup lang="ts">
import DocumentFilters from "./DocumentFilters.vue";
import {
  emptyDocumentSearch,
  documentQuery,
  type DocumentSearch,
} from "./documentSearch";
import { activeEntity } from "./entityScope";
import { onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { quantity, type PageData } from "./business";
const emit = defineEmits<{ failure: [error: unknown] }>();
interface Entry {
  id: string;
  warehouse_name: string;
  location_code: string;
  event_type: string;
  financial_kind: string | null;
  posted_at: string;
  name: string;
  base_unit: string;
  lot_no: string;
  qty_delta: string;
  amount_delta: string | null;
  posting_id: string;
  gross_amount: string | null;
  tax_amount: string | null;
}
const events: Record<string, string> = {
  RECEIPT_QUALIFIED: "采购入库",
  SALES_SHIPPED: "销售出库",
  RETAIL_SOLD: "零售出库",
  SALES_RETURNED: "销售退货入库",
  RETAIL_RETURNED: "零售退货入库",
  PURCHASE_RETURNED: "采购退货出库",
  COUNT_GAIN: "盘盈入库",
  OPENING_BALANCE: "期初启用",
  COUNT_LOSS: "盘亏出库",
  QUALITY_RELEASE: "质量放行",
  QUALITY_WRITE_OFF: "质量报损",
  TRANSFER_LOSS: "在途损失核销",
  LOCATION_MOVE: "同仓移库",
  TRANSFER_DISPATCH: "调拨发出",
  TRANSFER_RECEIVE: "调拨收货",
};
const eventLabel = (event: string) => events[event] || "业务出入库";
const financialLabel = (kind: string | null | undefined) =>
  (
    ({
      RECEIVABLE: "应收",
      PAYABLE: "应付",
      SALES_RETURN: "销售退货冲减",
      RETAIL_RETURN: "零售退货金额",
      PURCHASE_RETURN: "采购退货冲减",
    }) as Record<string, string>
  )[kind || ""] || "财务变动";
const entity = ref(activeEntity.value),
  offset = ref(0),
  loading = ref(false);
const data = ref<PageData<Entry>>({ items: [], has_more: false });
const selected = ref<Entry | null>(null);
const filters = ref(emptyDocumentSearch());
function applyFilters(value: DocumentSearch) {
  filters.value = value;
  selected.value = null;
  void load(true);
}
let epoch = 0;
onUnmounted(() => epoch++);
const listError = ref(false);
async function load(reset = false) {
  if (!entity.value) return;
  if (reset) offset.value = 0;
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<PageData<Entry>>(
      `/api/v1/inventory/ledger?${documentQuery(entity.value, offset.value, filters.value)}`,
    );
    if (current === epoch) {
      data.value = result;
      listError.value = false;
    }
  } catch (error) {
    if (current === epoch) {
      listError.value = true;
      emit("failure", error);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
const time = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
onMounted(() => load());
</script>
<template>
  <section class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button @click="load()">刷新台账</el-button>
    </div>
    <DocumentFilters
      :applied="filters"
      :states="events"
      placeholder="商品、编码、批号、仓库或过账编号"
      :disabled="loading"
      date-label="入账日期"
      state-label="库存动作"
      advanced-state
      @apply="applyFilters"
    />
    <p v-if="listError" role="alert" class="form-note">
      查询失败，当前保留上次成功结果；请重试查询。
    </p>
    <el-table
      :data="data.items"
      empty-text="暂无过账记录。完成采购入库或销售出库后，可在此查询。"
    >
      <el-table-column label="入账时间" min-width="170"
        ><template #default="{ row }">{{
          time(row.posted_at)
        }}</template></el-table-column
      >
      <el-table-column label="仓库 / 库位" min-width="160"
        ><template #default="{ row }"
          >{{ row.warehouse_name
          }}<small class="cell-sub">{{ row.location_code }}</small></template
        ></el-table-column
      >
      <el-table-column label="商品 / 批号" min-width="190"
        ><template #default="{ row }"
          ><b>{{ row.name }}</b
          ><small class="cell-sub">{{ row.lot_no }}</small></template
        ></el-table-column
      >
      <el-table-column label="库存动作" width="110"
        ><template #default="{ row }">{{
          eventLabel(row.event_type)
        }}</template></el-table-column
      >
      <el-table-column label="数量变动" min-width="115"
        ><template #default="{ row }"
          ><strong class="available"
            >{{ quantity(row.qty_delta) }} {{ row.base_unit }}</strong
          ></template
        ></el-table-column
      >
      <el-table-column label="本行成本（元）" min-width="140"
        ><template #default="{ row }">{{
          row.amount_delta === null ? "—" : quantity(row.amount_delta)
        }}</template></el-table-column
      >
      <el-table-column label="关联记录" width="130"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="selected = row">{{
            row.financial_kind === "RECEIVABLE"
              ? "查看过账应收"
              : row.financial_kind === "PAYABLE"
                ? "查看过账应付"
                : "查看过账记录"
          }}</el-button></template
        ></el-table-column
      >
    </el-table>
    <p class="table-note">
      金额以人民币记录。采购入库成本按未税金额登记；演示环境初始库存快照不包含历史台账。
    </p>
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
        :disabled="!data.has_more || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
  <el-dialog
    :model-value="!!selected"
    :title="
      selected?.financial_kind
        ? '过账与' + financialLabel(selected.financial_kind) + '记录'
        : '库存过账记录'
    "
    width="560px"
    class="business-dialog"
    @close="selected = null"
  >
    <template v-if="selected"
      ><dl class="facts">
        <dt>过账编号</dt>
        <dd class="mono">{{ selected.posting_id }}</dd>
        <dt v-if="selected.financial_kind">
          本次过账{{ financialLabel(selected.financial_kind) }}
        </dt>
        <dd v-if="selected.financial_kind">
          {{ selected.gross_amount ?? "—" }} 元
        </dd>
        <dt v-if="selected.financial_kind">本次过账税额</dt>
        <dd v-if="selected.financial_kind">
          {{ selected.tax_amount ?? "—" }} 元
        </dd>
      </dl>
      <p v-if="selected.financial_kind" class="form-note">
        以上为整次过账的应收或应付及税额，可能对应多条库存明细，不能逐行重复汇总。收付款及核销在“往来结算”办理，原始过账金额保持不变。
      </p></template
    >
  </el-dialog>
</template>
