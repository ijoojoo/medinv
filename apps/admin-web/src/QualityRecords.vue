<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { localTime } from "./sales";
const emit = defineEmits<{ failure: [error: unknown] }>();
type Row = {
  id: string;
  inspected_at: string;
  created_by: string;
  note: string;
  receipt_number: string;
  order_number: string;
  party_name: string;
  product_name: string;
  lot_no: string;
  base_unit: string;
  qualified_qty: string;
  rejected_qty: string;
};
const search = ref(""),
  offset = ref(0),
  loading = ref(false),
  failed = ref(false),
  data = ref<{ items: Row[]; has_more: boolean }>({
    items: [],
    has_more: false,
  });
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (reset) offset.value = 0;
  const token = ++epoch;
  loading.value = true;
  failed.value = false;
  try {
    const result = await request<typeof data.value>(
      `/api/v1/quality-records?entity_id=${activeEntity.value}&search=${encodeURIComponent(search.value)}&offset=${offset.value}`,
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
onMounted(() => load());
</script>
<template>
  <section class="panel" aria-label="质量记录" v-loading="loading">
    <header class="master-toolbar">
      <div>
        <h2>质量记录 · 采购验收记录</h2>
        <small>来源于实际验收单据，只读查询；记录不在此页面修改。</small>
      </div>
    </header>
    <form class="business-toolbar" @submit.prevent="load(true)">
      <label
        >查询<input
          v-model="search"
          placeholder="供应商、采购单号或收货单号"
          maxlength="100" /></label
      ><el-button native-type="submit">查询</el-button
      ><el-button
        @click="
          search = '';
          load(true);
        "
        >重置</el-button
      >
    </form>
    <p v-if="failed" role="alert">记录读取失败，请重试。</p>
    <div class="master-table-scroll">
      <table class="master-list-table">
        <thead>
          <tr>
            <th>验收时间</th>
            <th>采购单号</th>
            <th>收货单号</th>
            <th>供应商</th>
            <th>商品 / 批号</th>
            <th>单位</th>
            <th>合格数量</th>
            <th>拒收数量</th>
            <th>验收意见</th>
            <th>操作人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in data.items" :key="r.id">
            <td>{{ localTime(r.inspected_at) }}</td>
            <td>{{ r.order_number }}</td>
            <td>{{ r.receipt_number }}</td>
            <td>{{ r.party_name }}</td>
            <td>{{ r.product_name }} / {{ r.lot_no }}</td>
            <td>{{ r.base_unit }}</td>
            <td>{{ r.qualified_qty }}</td>
            <td>{{ r.rejected_qty }}</td>
            <td>{{ r.note }}</td>
            <td>{{ r.created_by }}</td>
          </tr>
          <tr v-if="!data.items.length">
            <td colspan="10" class="master-empty-tab">暂无验收记录</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="master-pagination">
      <el-button
        :disabled="!offset || loading"
        @click="
          offset -= 50;
          load();
        "
        >上一页</el-button
      ><span>{{ Math.floor(offset / 50) + 1 }} 页</span
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
</template>
