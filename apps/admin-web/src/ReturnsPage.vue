<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { quantity, useCommand, type PageData } from "./business";
import { localTime } from "./sales";
import {
  returnKinds,
  canApplyReturn,
  returnStates,
  type ReturnSource,
  type ReturnDocument,
  type ReturnLine,
  type ReturnSourceDetail,
} from "./returns";
import ReturnDetail from "./ReturnDetail.vue";
const props = defineProps<{
  initialId?: string;
  initialSourceId?: string;
  initialEntityId?: string;
  embedded?: boolean;
}>();
const emit = defineEmits<{ failure: [error: unknown]; back: [] }>();
const focusId = ref(props.initialSourceId || "");
const focused = ref<ReturnSourceDetail | null>(null);
const entity = ref(props.initialEntityId || activeEntity.value),
  tab = ref("DOCUMENTS"),
  offset = ref(0),
  loading = ref(false),
  selected = ref(props.initialId || "");
const sources = ref<PageData<ReturnSource>>({ items: [], has_more: false }),
  documents = ref<PageData<ReturnDocument>>({ items: [], has_more: false });
const source = ref<ReturnSource | null>(null),
  reason = ref(""),
  operation = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
const sourceLoading = ref(false),
  partialSupported = ref(false);
const requested = ref<(ReturnLine & { chosen: boolean; qty: string })[]>([]);
const isDocuments = computed(
  () => !!focusId.value || ["DOCUMENTS", "CUSTODY"].includes(tab.value),
);
const canApply = computed(
  () =>
    focused.value &&
    !focused.value.active_return &&
    focused.value.lines.some((r) => /[1-9]/.test(r.remaining_qty)),
);
const hasSelection = computed(() => requested.value.some((r) => r.chosen));
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    selected.value = "";
    sources.value = { items: [], has_more: false };
    documents.value = { items: [], has_more: false };
  }
  const current = ++epoch;
  loading.value = true;
  try {
    if (focusId.value) {
      const result = await request<ReturnSourceDetail>(
        `/api/v1/returns/sources/${focusId.value}?offset=${offset.value}`,
      );
      if (current === epoch) {
        focused.value = result;
        entity.value = result.source.entity_id;
        tab.value = result.source.kind;
        documents.value = result.history;
      }
      return;
    }
    const rows = await request<
      PageData<ReturnDocument> | PageData<ReturnSource>
    >(
      isDocuments.value
        ? "/api/v1/returns?entity_id=" +
            entity.value +
            "&offset=" +
            offset.value +
            (tab.value === "CUSTODY" ? "&custody_only=true" : "")
        : "/api/v1/returns/sources?entity_id=" +
            entity.value +
            "&kind=" +
            tab.value +
            "&offset=" +
            offset.value,
    );
    if (current === epoch) {
      if (isDocuments.value) documents.value = rows as PageData<ReturnDocument>;
      else sources.value = rows as PageData<ReturnSource>;
    }
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function showSource(row: ReturnSource) {
  focusId.value = row.posting_id;
  focused.value = null;
  await load(true);
}
async function leaveSource() {
  if (props.embedded) {
    emit("back");
    return;
  }
  focusId.value = "";
  focused.value = null;
  await load(true);
}
async function backFromReturn() {
  selected.value = "";
  if (!focusId.value) tab.value = "DOCUMENTS";
  await load(true);
}
async function begin(row: ReturnSource) {
  source.value = row;
  reason.value = "";
  operation.value = crypto.randomUUID();
  requested.value = [];
  partialSupported.value = false;
  sourceLoading.value = true;
  const current = epoch;
  try {
    const result = await request<ReturnSourceDetail>(
      "/api/v1/returns/sources/" + row.posting_id,
    );
    if (current !== epoch || source.value?.posting_id !== row.posting_id)
      return;
    source.value = result.source;
    if (result.active_return) {
      source.value = null;
      selected.value = result.active_return.id;
      return;
    }
    if (!result.lines.some((r) => /[1-9]/.test(r.remaining_qty))) {
      source.value = null;
      await showSource(result.source);
      return;
    }
    partialSupported.value = result.partial_supported;
    requested.value = result.lines.map((r) => ({
      ...r,
      chosen: /[1-9]/.test(r.remaining_qty),
      qty: quantity(r.remaining_qty),
    }));
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) sourceLoading.value = false;
  }
}
async function create() {
  if (!source.value || sourceLoading.value || !hasSelection.value) return;
  await execute<{ id: string }>(
    "/api/v1/returns",
    {
      client_operation_id: operation.value,
      source_posting_id: source.value.posting_id,
      reason: reason.value,
      lines: requested.value
        .filter((r) => r.chosen)
        .map((r) => ({
          source_posting_line_id: r.id,
          qty: r.qty,
        })),
    },
    async (result) => {
      source.value = null;
      selected.value = result.id;
    },
  );
}
onMounted(async () => {
  if (props.initialId) return;
  await load();
  if (
    props.initialSourceId &&
    !props.embedded &&
    focused.value &&
    canApplyReturn(focused.value.source.kind)
  ) {
    if (focused.value.active_return)
      selected.value = focused.value.active_return.id;
    else if (canApply.value) await begin(focused.value.source);
  }
});
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: !!source.value, busy: busy.value }),
    "returns",
  ),
);
</script>
<template>
  <ReturnDetail
    v-if="selected"
    :key="selected"
    :id="selected"
    @failure="emit('failure', $event)"
    @back="backFromReturn"
  />
  <section v-else class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button v-if="focusId" @click="leaveSource">{{
        embedded ? "返回原单" : "返回退货来源"
      }}</el-button>

      <el-button @click="load()">刷新退货</el-button>
    </div>
    <template v-if="focused">
      <div class="panel-heading">
        <div>
          <small>{{ returnKinds[focused.source.kind] }} · 原单退货进度</small>
          <h3 class="document-number">{{ focused.source.source_number }}</h3>
        </div>
        <el-button
          v-if="canApplyReturn(focused.source.kind)"
          type="primary"
          :disabled="loading || !canApply"
          @click="begin(focused.source)"
          >申请本次退货</el-button
        >
      </div>
      <p>
        {{ focused.source.party_name }} ·
        {{ localTime(focused.source.posted_at) }}
      </p>
      <p data-testid="source-return-amount">
        本次原单金额 {{ focused.source.gross_amount }} 元 · 已完成退货
        {{ focused.source.returned_amount }} 元
      </p>
      <div v-if="focused.active_return" class="business-toolbar">
        <span
          >待处理：{{ focused.active_return.number }} ·
          {{ returnStates[focused.active_return.state] }}</span
        >
        <el-button type="primary" @click="selected = focused.active_return!.id"
          >继续办理退货</el-button
        >
      </div>
      <p class="form-note">
        可退数量按已完成退货扣减；待处理申请完成或取消后才能新开。退货不会重新打开原采购或销售订单，退款按原退货单独办理。
      </p>
      <el-table :data="focused.lines" data-testid="source-return-lines">
        <el-table-column prop="name" label="商品" min-width="160" />
        <el-table-column prop="lot_no" label="原批号" min-width="140" />
        <el-table-column
          v-for="col in [
            { field: 'base_qty', name: '原数量' },
            { field: 'returned_qty', name: '已退数量' },
            { field: 'remaining_qty', name: '剩余可退' },
          ]"
          :key="col.field"
          :label="col.name"
          min-width="110"
        >
          <template #default="{ row }"
            >{{ quantity(row[col.field]) }} {{ row.base_unit }}</template
          >
        </el-table-column>
      </el-table>
      <h3>本原单退货记录</h3>
    </template>
    <div
      v-if="!focusId"
      class="finance-tabs"
      role="tablist"
      aria-label="退货业务"
    >
      <button
        v-for="item in [
          { key: 'DOCUMENTS', label: '退货记录' },
          { key: 'CUSTODY', label: '隔离保管台账' },
          { key: 'SALES', label: '销售退货来源' },
          { key: 'PURCHASE', label: '采购退货来源' },
          { key: 'RETAIL', label: '零售退货来源' },
        ]"
        :key="item.key"
        role="tab"
        :aria-selected="tab === item.key"
        :class="{ active: tab === item.key }"
        @click="
          tab = item.key;
          load(true);
        "
      >
        {{ item.label }}
      </button>
    </div>
    <p class="form-note">
      按原商品行选择退回数量，可分多次退货。销售退回品先登记隔离收货，再由独立人员验收；未验收保管品不计可售库存和自有成本。采购退货需原库位有足量可用库存。
    </p>
    <el-table
      v-if="isDocuments"
      :data="documents.items"
      :empty-text="
        focusId ? '本原单暂无退货记录' : '暂无退货，请在来源页选择原单'
      "
    >
      <el-table-column prop="number" label="退货单号" min-width="240" />
      <el-table-column
        v-if="!focusId"
        prop="party_name"
        label="往来单位 / 顾客"
        min-width="180"
      />
      <el-table-column label="类型" width="100"
        ><template #default="{ row }">{{
          returnKinds[row.kind]
        }}</template></el-table-column
      >
      <el-table-column prop="gross_amount" label="退货金额（元）" width="145" />
      <el-table-column label="状态" width="100"
        ><template #default="{ row }">{{
          returnStates[row.state]
        }}</template></el-table-column
      >
      <el-table-column
        v-if="tab === 'CUSTODY'"
        prop="zone_label"
        label="隔离保管位置"
        min-width="180"
      />
      <el-table-column label="操作" width="100"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="selected = row.id"
            >退货详情</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-table v-else :data="sources.items" empty-text="暂无已过账原单">
      <el-table-column label="来源单据 / 过账时间" min-width="270"
        ><template #default="{ row }"
          ><span class="mono">{{ row.source_number }}</span
          ><small class="cell-sub">{{
            localTime(row.posted_at)
          }}</small></template
        ></el-table-column
      >
      <el-table-column
        prop="party_name"
        label="往来单位 / 顾客"
        min-width="180"
      />
      <el-table-column prop="gross_amount" label="原单金额（元）" width="140" />
      <el-table-column
        prop="returned_amount"
        label="已退金额（元）"
        width="130"
      />
      <el-table-column label="退货情况" min-width="120"
        ><template #default="{ row }">{{
          row.return_state ? returnStates[row.return_state] : "无待处理申请"
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="200"
        ><template #default="{ row }">
          <el-button link type="primary" @click="showSource(row)"
            >查看进度</el-button
          >
          <el-button
            v-if="canApplyReturn(row.kind)"
            link
            type="primary"
            :disabled="!!row.return_state"
            @click="begin(row)"
            >申请退货</el-button
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
      >
      <el-button
        :disabled="!(isDocuments ? documents : sources).has_more || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
  <el-dialog
    :model-value="!!source"
    title="申请退货"
    width="900px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    @close="source = null"
  >
    <form
      class="business-form"
      v-loading="sourceLoading"
      @submit.prevent="create"
    >
      <p>
        {{ source?.source_number }} · {{ source?.party_name }} ·
        {{ source?.gross_amount }} 元
      </p>
      <p class="form-note">
        勾选商品并填写本次退货数量，金额由原过账行累计分摊，末次结清尾差。创建后请核对申请金额；申请不会自动退款。
      </p>
      <p v-if="!sourceLoading && !partialSupported" class="form-note">
        历史原单缺少逐行金额快照，仅支持整批退货。
      </p>
      <el-table :data="requested">
        <el-table-column label="选择" width="70"
          ><template #default="{ row, $index }">
            <input
              type="checkbox"
              v-model="row.chosen"
              :aria-label="'选择退货行 ' + ($index + 1)"
              :disabled="!partialSupported || !/[1-9]/.test(row.remaining_qty)"
            /> </template
        ></el-table-column>
        <el-table-column prop="name" label="商品" min-width="150" />
        <el-table-column prop="lot_no" label="原批号" min-width="140" />
        <el-table-column label="已退 / 可退" width="130"
          ><template #default="{ row }">
            {{ quantity(row.returned_qty) }} / {{ quantity(row.remaining_qty) }}
            {{ row.base_unit }}
          </template></el-table-column
        >
        <el-table-column label="本次数量" min-width="140"
          ><template #default="{ row, $index }">
            <input
              v-model="row.qty"
              :aria-label="'本次退货数量 ' + ($index + 1)"
              :required="row.chosen"
              :disabled="!row.chosen || !partialSupported"
              inputmode="decimal"
              pattern="[0-9]+([.][0-9]{1,6})?"
              style="width: 100%; box-sizing: border-box"
            /> </template
        ></el-table-column>
      </el-table>
      <label
        >退货原因<textarea v-model="reason" required maxlength="200" />
      </label>
      <div class="form-actions">
        <el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="sourceLoading || !hasSelection"
          >创建退货申请</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
