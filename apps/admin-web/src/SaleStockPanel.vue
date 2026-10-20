<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { quantity } from "./business";
import { localTime } from "./sales";
import {
  suggestShipment,
  type SaleStockSnapshot,
  type UnallocatedLine,
} from "./saleStock";

const props = withDefaults(
  defineProps<{
    channel: "wholesale" | "retail";
    entity: string;
    warehouse: string;
    skus: string[];
    minimumShelfDays?: number;
    lines?: UnallocatedLine[];
    disabled?: boolean;
  }>(),
  { minimumShelfDays: 0 },
);
const emit = defineEmits<{
  fill: [lines: { order_line_id: string; qty: string }[]];
}>();
const snapshot = ref<SaleStockSnapshot | null>(null);
const loading = ref(false),
  error = ref("");
const ids = computed(() => [...new Set(props.skus.filter(Boolean))]);
const query = computed(() => {
  if (
    !props.entity ||
    !props.warehouse ||
    !ids.value.length ||
    !Number.isInteger(props.minimumShelfDays) ||
    props.minimumShelfDays < 0 ||
    props.minimumShelfDays > 3650
  )
    return "";
  const params = new URLSearchParams({
    entity_id: props.entity,
    warehouse_id: props.warehouse,
  });
  for (const id of ids.value) params.append("sku_id", id);
  if (props.channel === "wholesale")
    params.set("minimum_shelf_days", String(props.minimumShelfDays));
  return `/api/v1/${props.channel === "retail" ? "retail" : "selling"}/stock?${params}`;
});
let epoch = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
async function refresh() {
  clearTimeout(timer);
  const path = query.value,
    current = ++epoch;
  snapshot.value = null;
  error.value = "";
  if (!path) {
    loading.value = false;
    return null;
  }
  loading.value = true;
  try {
    const result = await request<SaleStockSnapshot>(path);
    if (current !== epoch) return null;
    snapshot.value = result;
    return result;
  } catch {
    if (current === epoch) error.value = "库存查询失败，请刷新可售库存后重试。";
    return null;
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function fill() {
  const result = await refresh();
  if (!result || !props.lines || props.disabled) return;
  try {
    emit("fill", suggestShipment(props.lines, result.items));
  } catch {
    error.value = "库存数量格式异常，请刷新后重试。";
  }
}
watch(
  query,
  () => {
    epoch++;
    clearTimeout(timer);
    snapshot.value = null;
    error.value = "";
    loading.value = !!query.value;
    if (query.value) timer = setTimeout(() => void refresh(), 150);
  },
  { immediate: true, flush: "sync" },
);
onUnmounted(() => {
  epoch++;
  clearTimeout(timer);
});
</script>

<template>
  <section v-if="ids.length" class="sale-stock" aria-label="仓库可售库存">
    <div class="line-heading">
      <b>仓库可售库存（基本单位）</b>
      <div>
        <el-button :disabled="disabled || loading || !query" @click="refresh"
          >刷新可售库存</el-button
        >
        <el-button
          v-if="lines"
          type="primary"
          plain
          :disabled="disabled || loading || !query"
          @click="fill"
          >按可售量填入本次发货</el-button
        >
      </div>
    </div>
    <p v-if="!query" class="form-note">请选择仓库并填写有效的最低效期要求。</p>
    <p v-else-if="loading" role="status">正在查询可售库存…</p>
    <el-alert
      v-else-if="error"
      :title="error"
      type="warning"
      :closable="false"
    />
    <template v-else-if="snapshot">
      <el-table :data="snapshot.items" size="small" max-height="260">
        <el-table-column prop="name" label="商品" min-width="160" />
        <el-table-column prop="code" label="SKU 编码" min-width="140" />
        <el-table-column label="可售数量" min-width="150">
          <template #default="{ row }">
            {{ quantity(row.available_qty) }} {{ row.base_unit }}
            <span v-if="!row.sale_eligible"> · 商品暂不可售</span>
          </template>
        </el-table-column>
      </el-table>
      <p class="form-note">
        查询时间：{{ localTime(snapshot.data_as_of)
        }}<template v-if="channel === 'wholesale'">
          · 最低剩余效期 {{ snapshot.minimum_shelf_days }} 天</template
        >。已扣除预占及不可售库存，数量以基本单位显示。查询不占库存，提交时会再次校验。
      </p>
    </template>
  </section>
</template>

<style scoped>
.sale-stock {
  margin: 18px 0;
  padding: 16px;
  border: 1px solid #dce7e4;
  border-radius: 10px;
  background: #f7faf9;
}
.line-heading {
  flex-wrap: wrap;
  gap: 10px;
}
.line-heading > div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.line-heading .el-button + .el-button {
  margin-left: 0;
}
</style>
