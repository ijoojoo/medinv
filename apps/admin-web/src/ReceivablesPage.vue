<script setup lang="ts">
import { activeEntity } from "./entityScope";
import { onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { quantity, type PageData } from "./business";
import { localTime } from "./sales";
const emit = defineEmits<{ failure: [error: unknown] }>();
interface Entry {
  id: string;
  customer_name: string;
  shipment_number: string;
  gross_amount: string;
  settled_amount: string;
  returned_amount: string;
  outstanding_amount: string;
  net_amount: string;
  tax_amount: string;
  cost_amount: string;
  due_date: string;
  created_at: string;
  posting_id: string;
}
const entity = ref(activeEntity.value),
  offset = ref(0),
  loading = ref(false);
const data = ref<PageData<Entry>>({ items: [], has_more: false }),
  selected = ref<Entry | null>(null);
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (!entity.value) return;
  if (reset) {
    offset.value = 0;
    data.value = { items: [], has_more: false };
    selected.value = null;
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<PageData<Entry>>(
      `/api/v1/receivables?entity_id=${entity.value}&offset=${offset.value}`,
    );
    if (current === epoch) data.value = result;
  } catch (error) {
    if (current === epoch) emit("failure", error);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
onMounted(() => load());
</script>
<template>
  <section class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button @click="load()">刷新应收</el-button>
    </div>
    <el-table
      :data="data.items"
      empty-text="暂无销售应收，完成销售出库后自动生成"
    >
      <el-table-column
        prop="customer_name"
        label="客户"
        min-width="160"
      /><el-table-column
        prop="shipment_number"
        label="发货单号"
        min-width="240"
      /><el-table-column
        prop="gross_amount"
        label="应收金额（元）"
        min-width="135"
      /><el-table-column
        prop="returned_amount"
        label="已退货（元）"
        min-width="125"
      /><el-table-column
        prop="outstanding_amount"
        label="未结金额（元）"
        min-width="130"
      /><el-table-column
        prop="due_date"
        label="到期日"
        width="120"
      /><el-table-column label="操作" width="110"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="selected = row"
            >应收详情</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <p class="table-note">
      每张发货子单仅产生一笔应收。原始应收保留不变，未结余额随核销、撤销及退货更新；在“往来结算”登记收款并核销。
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
    title="销售应收详情"
    width="620px"
    class="business-dialog"
    @close="selected = null"
  >
    <dl v-if="selected" class="facts">
      <dt>客户</dt>
      <dd>{{ selected.customer_name }}</dd>
      <dt>发货子单</dt>
      <dd>{{ selected.shipment_number }}</dd>
      <dt>应收金额</dt>
      <dd>{{ selected.gross_amount }} 元</dd>
      <dt>已核销 / 未结余额</dt>
      <dd>
        {{ selected.settled_amount }} / {{ selected.outstanding_amount }} 元
      </dd>
      <dt>已退货金额</dt>
      <dd>{{ selected.returned_amount }} 元</dd>
      <dt>未税金额 / 税额</dt>
      <dd>{{ selected.net_amount }} / {{ selected.tax_amount }} 元</dd>
      <dt>出库成本</dt>
      <dd>{{ quantity(selected.cost_amount) }} 元</dd>
      <dt>到期日</dt>
      <dd>{{ selected.due_date }}</dd>
      <dt>入账时间</dt>
      <dd>{{ localTime(selected.created_at) }}</dd>
      <dt>过账编号</dt>
      <dd class="mono">{{ selected.posting_id }}</dd>
    </dl>
  </el-dialog>
</template>
