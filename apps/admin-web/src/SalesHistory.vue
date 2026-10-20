<script setup lang="ts">
import { onMounted, ref } from "vue";
import { request, session } from "./api";
import { localTime } from "./sales";
import { quantity, type PageData } from "./business";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
interface HistoryEvent {
  id: string;
  created_by: string;
  created_at: string;
  action: string;
  detail: Record<string, any>;
}
const data = ref<PageData<HistoryEvent>>({ items: [], has_more: false });
const offset = ref(0),
  loading = ref(false);
const labels: Record<string, string> = {
  "sales.created": "创建销售单",
  "sales.revised": "修改销售单",
  "sales.approved": "审核通过",
  "sales.cancelled": "取消销售单",
  "sales.closed": "关闭未发余量",
};
async function load() {
  loading.value = true;
  try {
    data.value = await request<PageData<HistoryEvent>>(
      `/api/v1/sales-orders/${props.id}/history?offset=${offset.value}`,
    );
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <section
    class="panel sales-history"
    data-testid="sales-history"
    v-loading="loading"
  >
    <h3>销售变更与审核记录</h3>
    <p v-if="!data.items.length" class="form-note">暂无记录。</p>
    <details
      v-for="(event, index) in data.items"
      :key="event.id"
      :open="index === 0 && event.action === 'sales.revised'"
    >
      <summary>
        {{ localTime(event.created_at) }} ·
        {{ labels[event.action] || "销售操作" }}
        <span v-if="event.detail.revision_no">
          · 第 {{ event.detail.revision_no }} 版</span
        >
        · {{ event.created_by === session?.principal_id ? "本人" : "其他成员" }}
      </summary>
      <p>
        {{ event.detail.reason || event.detail.close_reason || "原始业务记录" }}
      </p>
      <p v-if="event.detail.released_exposure">
        已释放原信用占用 {{ event.detail.released_exposure }} 元
      </p>
      <template v-for="part in ['before', 'after', 'agreement']" :key="part">
        <div v-if="event.detail[part]" class="agreement">
          <b>{{
            part === "before"
              ? "修改前"
              : part === "after"
                ? "修改后"
                : "本次销售内容"
          }}</b>
          <p>
            客户：{{ event.detail[part].order.customer_name }} · 发货仓库：{{
              event.detail[part].order.warehouse_name
            }}
          </p>
          <p>
            收货地址：{{ event.detail[part].order.delivery_address }} ·
            最低剩余效期 {{ event.detail[part].order.minimum_shelf_days }} 天 ·
            账期 {{ event.detail[part].order.credit_days }} 天
          </p>
          <table>
            <thead>
              <tr>
                <th>商品</th>
                <th>交易数量 / 单位</th>
                <th>基本数量</th>
                <th>含税单价</th>
                <th>税率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="line in event.detail[part].lines" :key="line.id">
                <td>
                  {{ line.name }}<small>{{ line.code }}</small>
                </td>
                <td>
                  {{ quantity(line.trade_qty || line.qty) }}
                  {{ line.package_snapshot?.unit_name || line.base_unit }}
                </td>
                <td>{{ quantity(line.qty) }} {{ line.base_unit }}</td>
                <td>{{ quantity(line.unit_price) }}</td>
                <td>{{ quantity(line.tax_rate) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <small>操作人 ID：{{ event.created_by }}</small>
    </details>
    <div class="pager">
      <span>每页最多 50 条记录</span>
      <el-button
        :disabled="!offset || loading"
        @click="
          offset -= 50;
          load();
        "
        >上一页记录</el-button
      >
      <el-button
        :disabled="!data.has_more || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页记录</el-button
      >
    </div>
  </section>
</template>
<style scoped>
.sales-history {
  margin-top: 24px;
}
details {
  border-top: 1px solid var(--line);
  padding: 12px 0;
}
summary {
  cursor: pointer;
  line-height: 1.6;
}
table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  margin: 12px 0;
}
th,
td {
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere;
  vertical-align: top;
}
small {
  display: block;
  overflow-wrap: anywhere;
  color: var(--muted);
}
.agreement {
  margin: 16px 0;
}
</style>
