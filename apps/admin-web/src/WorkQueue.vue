<script setup lang="ts">
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { localTime } from "./sales";
import type { WorkQueueFilters, WorkQueueItem } from "./workQueue";

const props = defineProps<{ modelValue: WorkQueueFilters | null }>();
const emit = defineEmits<{
  "update:modelValue": [filters: WorkQueueFilters];
  open: [item: WorkQueueItem];
  failure: [error: unknown];
}>();
const saved =
  props.modelValue?.entity_id === activeEntity.value ? props.modelValue : null;
const entity = ref(activeEntity.value);
const kind = ref(saved?.kind || "all"),
  search = ref(saved?.search || "");
const offset = ref(saved?.offset || 0),
  loading = ref(false),
  failed = ref(false);
const data = ref<{
  items: WorkQueueItem[];
  has_more: boolean;
  kinds: { code: string; name: string }[];
}>({ items: [], has_more: false, kinds: [] });
const visible = computed(() =>
  [
    "purchase.read",
    "sales.read",
    "retail.read",
    "returns.read",
    "transfers.read",
    "counts.read",
    "quality.read",
    "inventory.read",
  ].some((p) => session.value?.permissions.includes(p)),
);
const names = computed(() =>
  Object.fromEntries(data.value.kinds.map((k) => [k.code, k.name])),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  const current = ++epoch;
  if (reset) offset.value = 0;
  data.value = { ...data.value, items: [], has_more: false };
  failed.value = false;
  loading.value = false;
  if (!entity.value || !visible.value) return;
  const filters = {
    entity_id: entity.value,
    kind: kind.value,
    search: search.value,
    offset: offset.value,
  };
  emit("update:modelValue", filters);
  loading.value = true;
  try {
    const result = await request<typeof data.value>(
      `/api/v1/work-queue?${new URLSearchParams({ ...filters, offset: String(filters.offset) })}`,
    );
    if (current === epoch) data.value = result;
  } catch (error) {
    if (current === epoch) {
      failed.value = true;
      emit("failure", error);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
onMounted(() => load());
</script>

<template>
  <section
    v-if="visible"
    class="panel work-queue"
    aria-label="进销存业务待办"
    v-loading="loading"
  >
    <div class="panel-heading">
      <div>
        <h3>进销存业务待办</h3>
      </div>
      <el-button :disabled="loading" @click="load()">刷新业务待办</el-button>
    </div>
    <p class="form-note">
      按经营主体集中处理未完成业务，进入原单核对后办理。审核、验收和复核仍须遵守原有权限及人员分工。
    </p>
    <form class="business-toolbar" @submit.prevent="load(true)">
      <label
        >待办环节<select v-model="kind" @change="load(true)">
          <option value="all">全部环节</option>
          <option v-for="k in data.kinds" :key="k.code" :value="k.code">
            {{ k.name }}
          </option>
        </select></label
      >
      <label
        >查找待办<input
          v-model="search"
          maxlength="100"
          placeholder="单号、往来单位、仓库名称"
      /></label>
      <el-button native-type="submit" :disabled="loading">查询待办</el-button>
    </form>
    <el-empty v-if="!entity" description="尚未分配经营主体，请联系企业管理员" />
    <el-alert
      v-else-if="failed"
      title="待办未能加载，请点击刷新重试。"
      type="warning"
      :closable="false"
    />
    <el-table
      v-else
      :data="data.items"
      max-height="520"
      :row-key="(row: WorkQueueItem) => row.kind + ':' + row.item_id"
      empty-text="当前条件下暂无待办"
    >
      <el-table-column label="待办环节" min-width="170"
        ><template #default="{ row }">
          <el-tag>{{ names[row.kind] }}</el-tag>
          <small v-if="row.reservation_expired" class="cell-sub"
            >预占已到期，请进入原单处理</small
          >
          <small v-if="row.kind === 'return_refund_check'" class="cell-sub"
            >有可退原款项，请核对实际退款；不代表应退欠款</small
          >
          <small v-if="row.kind === 'transfer_receive'" class="cell-sub"
            >仍有未结在途；短少请在原单核对损失处理</small
          >
          <small v-if="row.kind.startsWith('transfer_loss_')" class="cell-sub"
            >申请不扣在途；实物已到齐仍须核对并取消遗留申请</small
          >
          <small v-if="row.page === 'quality'" class="cell-sub"
            >审核不改变库存；放行或报损过账后才生效</small
          >
          <small v-if="row.page === 'counts'" class="cell-sub"
            >所选主体批次盘点冻结中，过账或取消后解除</small
          >
          <small v-if="row.page === 'openings'" class="cell-sub"
            >期初启用后才可销售；请先由另一位有权限的成员核对</small
          >
        </template></el-table-column
      >
      <el-table-column label="原单 / 待办凭据" min-width="300"
        ><template #default="{ row }">
          <span class="mono">{{ row.number }}</span
          ><small v-if="row.related_number" class="cell-sub mono">{{
            row.related_number
          }}</small>
        </template></el-table-column
      >
      <el-table-column prop="party_name" label="往来 / 业务" min-width="150" />
      <el-table-column
        prop="warehouse_name"
        label="仓库 / 调拨路线"
        min-width="170"
      />
      <el-table-column label="单据时间" min-width="175"
        ><template #default="{ row }">{{
          localTime(row.document_time)
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="110" fixed="right"
        ><template #default="{ row }">
          <el-button link type="primary" @click="emit('open', row)"
            >查看原单</el-button
          >
        </template></el-table-column
      >
    </el-table>
    <div class="pager">
      <span>每页50条，单据时间从早到晚 · 第 {{ offset / 50 + 1 }} 页</span>
      <el-button
        :disabled="!offset || loading"
        @click="
          offset -= 50;
          load();
        "
        >上一页待办</el-button
      >
      <el-button
        :disabled="!data.has_more || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页待办</el-button
      >
    </div>
  </section>
</template>

<style scoped>
.work-queue {
  margin-bottom: 24px;
}
.work-queue input {
  min-width: 250px;
}
@media (max-width: 680px) {
  .work-queue input {
    min-width: 0;
    width: 100%;
  }
}
</style>
