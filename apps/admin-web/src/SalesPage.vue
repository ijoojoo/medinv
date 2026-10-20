<script setup lang="ts">
import { activeEntity } from "./entityScope";
import EditorDrafts from "./EditorDrafts.vue";
import {
  createEditor,
  recordNavigation,
  activeRecord,
  recordSaved,
  showRecord,
  goPage,
  listCacheKey,
} from "./workspace";
import { onMounted, onUnmounted, ref, watch } from "vue";
import { request, session } from "./api";
import { can, loadOptions, type PageData } from "./business";
import {
  localTime,
  salesLabels,
  type SalesOptions,
  type SalesOrder,
} from "./sales";
import DocumentFilters from "./DocumentFilters.vue";
import {
  documentQuery,
  emptyDocumentSearch,
  type DocumentSearch,
} from "./documentSearch";
import CreditPanel from "./CreditPanel.vue";
import SalesDetail from "./SalesDetail.vue";
const props = defineProps<{ initialEntityId?: string; initialId?: string }>();
const emit = defineEmits<{
  failure: [error: unknown];
  purchase: [source: { id: string; entity_id: string; sale_id: string }];
}>();
const entity = ref(props.initialEntityId || activeEntity.value),
  offset = ref(0),
  loading = ref(false);
const detailRefresh = ref(0);
const loadError = ref(false);
const selected = ref(props.initialId || ""),
  creating = ref(false);
const data = ref<PageData<SalesOrder>>({ items: [], has_more: false });
const filters = ref(emptyDocumentSearch());
function applyFilters(value: DocumentSearch) {
  filters.value = value;
  void load(true);
}
function changeEntity() {
  filters.value = emptyDocumentSearch();
  void load(true);
}
const options = ref<SalesOptions>({ customers: [], skus: [], warehouses: [] });
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (!entity.value) return;
  if (reset) {
    offset.value = 0;
    selected.value = "";
    options.value = { customers: [], skus: [], warehouses: [] };
  }
  const current = ++epoch;
  loading.value = true;
  loadError.value = false;
  try {
    const [rows, choices] = await Promise.all([
      request<PageData<SalesOrder>>(
        `/api/v1/sales-orders?${documentQuery(entity.value, offset.value, filters.value)}`,
      ),
      loadOptions<SalesOptions>(
        `/api/v1/selling/options?entity_id=${entity.value}&include_skus=false&include_parties=false`,
      ),
    ]);
    if (current === epoch) {
      data.value = rows;
      options.value = choices;
    }
  } catch (error) {
    if (current === epoch) {
      loadError.value = true;
      emit("failure", error);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function newOrder() {
  creating.value = true;
  try {
    await createEditor("sale", entity.value, {
      warehouse_id: options.value.warehouses[0]?.id || "",
    });
  } catch (e) {
    emit("failure", e);
  } finally {
    creating.value = false;
  }
}
function viewRecord(id: string) {
  showRecord("sale", id, entity.value);
}
function back() {
  selected.value = "";
  goPage("selling");
  void load();
}
watch(recordNavigation, (target) => {
  if (activeRecord.value) return;
  if (target?.channel !== "sale") return;
  if (entity.value !== target.entity) {
    entity.value = target.entity;
    filters.value = emptyDocumentSearch();
    offset.value = 0;
  }
  selected.value = target.id;
  void load();
});
watch(recordSaved, (target) => {
  if (target?.channel === "sale") {
    detailRefresh.value++;
    void load();
  }
});
try {
  const cached = JSON.parse(
    sessionStorage.getItem(listCacheKey("sale")) || "null",
  );
  if (cached && cached.entity === activeEntity.value) {
    entity.value = cached.entity;
    filters.value = { ...emptyDocumentSearch(), ...cached.filters };
    offset.value =
      Number.isSafeInteger(cached.offset) && cached.offset >= 0
        ? cached.offset
        : 0;
  }
} catch {
  /* Defaults remain usable when browser storage is unavailable. */
}
watch(
  [entity, offset, filters],
  () => {
    try {
      sessionStorage.setItem(
        listCacheKey("sale"),
        JSON.stringify({
          entity: entity.value,
          offset: offset.value,
          filters: filters.value,
        }),
      );
    } catch {
      /* Server drafts do not depend on list preferences. */
    }
  },
  { deep: true },
);
watch(
  () => [props.initialId, props.initialEntityId],
  () => {
    if (!props.initialId) return;
    if (props.initialEntityId) entity.value = props.initialEntityId;
    selected.value = props.initialId;
    detailRefresh.value++;
    void load();
  },
);
onMounted(() => {
  const target = recordNavigation.value;
  if (target?.channel === "sale" && !activeRecord.value) {
    entity.value = target.entity;
    selected.value = target.id;
  }
  void load();
});
</script>
<template>
  <SalesDetail
    v-if="selected"
    :key="selected + detailRefresh"
    :id="selected"
    @failure="emit('failure', $event)"
    @purchase="emit('purchase', $event)"
    @back="back"
  />
  <section v-show="!selected" class="panel" v-loading="loading">
    <div class="business-toolbar">
      <h2 class="business-page-title">销售单</h2>
      <el-button @click="load()">刷新销售单</el-button>
      <el-button
        v-if="can('sales.write')"
        type="primary"
        @click="newOrder"
        :loading="creating"
        >新建销售单</el-button
      >
      <EditorDrafts
        v-if="can('sales.write')"
        channel="sale"
        :entity="entity"
        @failure="emit('failure', $event)"
      />
    </div>
    <DocumentFilters
      :entity="entity"
      channel="sale"
      :warehouses="options.warehouses"
      :key="entity"
      :applied="filters"
      :disabled="loading"
      :states="{
        DRAFT: '待审核',
        APPROVED: '待发齐',
        FULFILLED: '已发齐',
        CLOSED: '余量已关闭',
        CANCELLED: '已取消',
      }"
      placeholder="销售单号、客户名称或编码"
      @apply="applyFilters"
    />
    <div class="flow-strip">
      <span>01 客户授信 / 制单</span><span>02 独立审核</span
      ><span>03 按效期拣货</span><span>04 复核出库 / 应收</span>
    </div>
    <CreditPanel
      :key="entity"
      :entity="entity"
      @failure="emit('failure', $event)"
    />
    <el-alert
      v-if="loadError"
      title="查询失败，请重试；表格保留上次成功加载的结果。"
      type="error"
      :closable="false"
    />
    <el-table
      :data="data.items"
      empty-text="没有符合条件的销售单，可调整条件或清除筛选"
    >
      <el-table-column prop="number" label="销售单号" min-width="240" />
      <el-table-column prop="customer_name" label="客户" min-width="160" />
      <el-table-column prop="warehouse_name" label="发货仓库" min-width="120" />
      <el-table-column label="创建时间" min-width="170"
        ><template #default="{ row }">{{
          localTime(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column
        prop="gross_amount"
        label="含税金额（元）"
        min-width="135"
      />
      <el-table-column label="状态" width="110"
        ><template #default="{ row }"
          ><el-tag :type="row.state === 'FULFILLED' ? 'success' : 'info'">{{
            salesLabels[row.state]
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="操作" width="110"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="viewRecord(row.id)"
            >查看销售单</el-button
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
