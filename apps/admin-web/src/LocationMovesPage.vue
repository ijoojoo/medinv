<script setup lang="ts">
import DocumentFilters from "./DocumentFilters.vue";
import {
  emptyDocumentSearch,
  documentQuery,
  type DocumentSearch,
} from "./documentSearch";
const filters = ref(emptyDocumentSearch());
function applyFilters(value: DocumentSearch) {
  filters.value = value;
  void load(true);
}
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import {
  can,
  quantity,
  loadOptions,
  useCommand,
  type PageData,
} from "./business";
import { localTime } from "./sales";
interface Candidate {
  id: string;
  name: string;
  sku_code: string;
  lot_no: string;
  warehouse_name: string;
  location_code: string;
  available: string;
  base_unit: string;
}
interface Move {
  id: string;
  number: string;
  name: string;
  lot_no: string;
  warehouse_name: string;
  source_location: string;
  source_name: string;
  destination_location: string;
  destination_name: string;
  qty: string;
  base_unit: string;
  state: string;
  version: string;
  created_by: string;
  reason: string;
  created_at: string;
  review_note: string | null;
  cancel_note: string | null;
}
interface Detail {
  document: Move;
  result: { posting_id: string; qty: string; cost_delta: string } | null;
}
const states: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "待移动",
  POSTED: "已移动",
  CANCELLED: "已取消",
};
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(activeEntity.value),
  creating = ref(false),
  loading = ref(false),
  offset = ref(0),
  sourceOffset = ref(0),
  search = ref("");
const documents = ref<PageData<Move>>({ items: [], has_more: false }),
  candidates = ref<PageData<Candidate>>({ items: [], has_more: false });
const detail = ref<Detail | null>(null),
  chosen = ref<Candidate | null>(null),
  locations = ref<{ id: string; code: string; name: string }[]>([]),
  destination = ref(""),
  qty = ref(""),
  lot = ref(""),
  reason = ref("");
const mode = ref(""),
  decision = ref("APPROVE"),
  actionReason = ref(""),
  actualLot = ref(""),
  confirmed = ref(false),
  locationsLoading = ref(false);
const titles: Record<string, string> = {
  review: "独立审核移库",
  post: "确认库位移动",
  cancel: "取消移库单",
};
const independent = computed(
  () => detail.value?.document.created_by !== session.value?.principal_id,
);
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  choiceEpoch = 0,
  operation = "";
onUnmounted(() => {
  epoch++;
  choiceEpoch++;
});
const listError = ref(false);
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    sourceOffset.value = 0;
    detail.value = null;
    creating.value = false;
    chosen.value = null;
    choiceEpoch++;
  }
  const current = ++epoch;
  loading.value = true;
  try {
    if (detail.value) {
      const result = await request<Detail>(
        `/api/v1/location-moves/${detail.value.document.id}`,
      );
      if (current === epoch) detail.value = result;
    } else if (creating.value) {
      const result = await request<PageData<Candidate>>(
        `/api/v1/location-moves/candidates?entity_id=${entity.value}&search=${encodeURIComponent(search.value)}&offset=${sourceOffset.value}`,
      );
      if (current === epoch) candidates.value = result;
    } else {
      const result = await request<PageData<Move>>(
        `/api/v1/location-moves?${documentQuery(entity.value, offset.value, filters.value)}`,
      );
      if (current === epoch) {
        documents.value = result;
        listError.value = false;
      }
    }
  } catch (e) {
    if (current === epoch) {
      listError.value = true;
      emit("failure", e);
    }
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function open(id: string) {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<Detail>(`/api/v1/location-moves/${id}`);
    if (current === epoch) {
      detail.value = result;
      creating.value = false;
    }
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin() {
  creating.value = true;
  chosen.value = null;
  search.value = "";
  sourceOffset.value = 0;
  locations.value = [];
  choiceEpoch++;
  operation = crypto.randomUUID();
  load();
}
async function choose(row: Candidate) {
  chosen.value = row;
  destination.value = "";
  qty.value = "";
  lot.value = "";
  reason.value = "";
  locations.value = [];
  locationsLoading.value = true;
  const current = ++choiceEpoch;
  try {
    const result = await loadOptions<{
      items: { id: string; code: string; name: string }[];
    }>(
      `/api/v1/location-moves/destinations?entity_id=${entity.value}&balance_id=${row.id}`,
    );
    if (current === choiceEpoch) locations.value = result.items;
  } catch (e) {
    if (current === choiceEpoch) emit("failure", e);
  } finally {
    if (current === choiceEpoch) locationsLoading.value = false;
  }
}
async function create() {
  if (!chosen.value || locationsLoading.value || !destination.value) return;
  await execute<{ id: string }>(
    "/api/v1/location-moves",
    {
      client_operation_id: operation,
      entity_id: entity.value,
      balance_id: chosen.value.id,
      destination_location_id: destination.value,
      qty: qty.value,
      lot_no: lot.value,
      reason: reason.value,
    },
    async (result) => {
      await open(result.id);
    },
  );
}
function beginAction(action: string) {
  mode.value = action;
  decision.value = "APPROVE";
  actionReason.value = "";
  actualLot.value = "";
  confirmed.value = false;
}
async function submit() {
  if (!detail.value) return;
  await execute(
    `/api/v1/location-moves/${detail.value.document.id}/${mode.value}`,
    {
      expected_version: detail.value.document.version,
      reason: actionReason.value,
      ...(mode.value === "review" ? { decision: decision.value } : {}),
      ...(mode.value === "post"
        ? { lot_no: actualLot.value, confirmed: confirmed.value }
        : {}),
    },
    async () => {
      mode.value = "";
      await load();
    },
  );
}
onMounted(() => load());
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: creating.value || !!mode.value, busy: busy.value }),
    "location-moves",
  ),
);
</script>
<template>
  <div class="business-toolbar">
    <el-button
      v-if="detail || creating"
      :disabled="busy || loading"
      @click="
        detail = null;
        creating = false;
        chosen = null;
        choiceEpoch++;
        load();
      "
      >返回移库列表</el-button
    >

    <el-button :disabled="busy || loading" @click="load()">刷新移库</el-button>
    <el-button
      v-if="!detail && !creating && can('transfers.write')"
      type="primary"
      :disabled="loading"
      @click="begin"
      >新建移库单</el-button
    >
  </div>
  <section v-if="detail" class="panel" v-loading="loading">
    <div class="panel-heading">
      <h3 class="document-number">{{ detail.document.number }}</h3>
      <el-tag>{{ states[detail.document.state] }}</el-tag>
    </div>
    <dl class="facts">
      <dt>商品 / 批号</dt>
      <dd>{{ detail.document.name }} / {{ detail.document.lot_no }}</dd>
      <dt>仓库</dt>
      <dd>{{ detail.document.warehouse_name }}</dd>
      <dt>来源库位</dt>
      <dd>
        {{ detail.document.source_location }} ·
        {{ detail.document.source_name }}
      </dd>
      <dt>目标库位</dt>
      <dd>
        {{ detail.document.destination_location }} ·
        {{ detail.document.destination_name }}
      </dd>
      <dt>移动数量</dt>
      <dd>
        {{ quantity(detail.document.qty) }} {{ detail.document.base_unit }}
      </dd>
      <dt>申请原因</dt>
      <dd>{{ detail.document.reason }}</dd>
      <dt>申请时间</dt>
      <dd>{{ localTime(detail.document.created_at) }}</dd>
      <template v-if="detail.document.review_note"
        ><dt>审核意见</dt>
        <dd>{{ detail.document.review_note }}</dd></template
      >
      <template v-if="detail.document.cancel_note"
        ><dt>取消原因</dt>
        <dd>{{ detail.document.cancel_note }}</dd></template
      >
    </dl>
    <p class="form-note">
      申请与审核不占用库存。移动前重新校验可用量、批次资质与盘点冻结；批号、质量状态和批次成本不变。
    </p>
    <div class="form-actions">
      <el-button
        v-if="
          detail.document.state === 'DRAFT' &&
          independent &&
          can('transfers.review')
        "
        :disabled="busy || loading"
        @click="beginAction('review')"
        >独立审核移库</el-button
      >
      <el-button
        v-if="detail.document.state === 'APPROVED' && can('transfers.dispatch')"
        type="primary"
        :disabled="busy || loading"
        @click="beginAction('post')"
        >确认库位移动</el-button
      >
      <el-button
        v-if="
          ['DRAFT', 'APPROVED'].includes(detail.document.state) &&
          can('transfers.write')
        "
        :disabled="busy || loading"
        @click="beginAction('cancel')"
        >取消移库单</el-button
      >
    </div>
    <el-alert
      v-if="detail.result"
      title="库位移动已完成，原库位扣减、目标库位增加，仓库总量与批次成本不变。"
      type="success"
      :closable="false"
    />
  </section>
  <section v-else-if="!creating" class="panel" v-loading="loading">
    <p class="form-note">
      在同一实体仓内移动自有合格库存，每单一个来源库位批次，可移动部分数量。跨仓业务请使用仓库调拨。
    </p>
    <DocumentFilters
      :applied="filters"
      :states="states"
      placeholder="单号、商品、批号、仓库或库位"
      :disabled="loading || busy"
      advanced-state
      @apply="applyFilters"
    />
    <p v-if="listError" role="alert" class="form-note">
      查询失败，当前保留上次成功结果；请重试查询。
    </p>
    <el-table :data="documents.items" empty-text="暂无移库单">
      <el-table-column prop="number" label="移库单号" min-width="250" />
      <el-table-column prop="name" label="商品" min-width="160" />
      <el-table-column prop="lot_no" label="批号" min-width="130" />
      <el-table-column label="仓库 / 库位" min-width="180"
        ><template #default="{ row }"
          >{{ row.warehouse_name
          }}<small class="cell-sub"
            >{{ row.source_location }} → {{ row.destination_location }}</small
          ></template
        ></el-table-column
      >
      <el-table-column label="数量" width="90"
        ><template #default="{ row }"
          >{{ quantity(row.qty) }} {{ row.base_unit }}</template
        ></el-table-column
      >
      <el-table-column label="状态" width="100"
        ><template #default="{ row }">{{
          states[row.state]
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="110"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="open(row.id)"
            >查看移库单</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <div class="pager">
      <el-button
        :disabled="loading || !offset"
        @click="
          offset -= 50;
          load();
        "
        >上一页</el-button
      ><span>每页最多 50 条</span
      ><el-button
        :disabled="loading || !documents.has_more"
        @click="
          offset += 50;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
  <template v-else>
    <section class="panel" v-loading="loading">
      <form
        class="business-toolbar"
        @submit.prevent="
          sourceOffset = 0;
          load();
        "
      >
        <label>商品 / 批号<input v-model="search" maxlength="100" /></label
        ><el-button native-type="submit" :disabled="busy || loading"
          >查找移库库存</el-button
        >
      </form>
      <el-table :data="candidates.items" empty-text="暂无可选择库存">
        <el-table-column
          prop="name"
          label="商品"
          min-width="160"
        /><el-table-column prop="lot_no" label="批号" min-width="130" />
        <el-table-column label="仓库 / 来源库位" min-width="180"
          ><template #default="{ row }"
            >{{ row.warehouse_name }} / {{ row.location_code }}</template
          ></el-table-column
        >
        <el-table-column label="未占用数量" width="130"
          ><template #default="{ row }"
            >{{ quantity(row.available) }} {{ row.base_unit }}</template
          ></el-table-column
        >
        <el-table-column label="操作" width="100"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :disabled="busy"
              @click="choose(row)"
              >选择批次</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <div class="pager">
        <el-button
          :disabled="loading || !sourceOffset"
          @click="
            sourceOffset -= 50;
            load();
          "
          >上一页</el-button
        ><span>每页最多 50 条</span
        ><el-button
          :disabled="loading || !candidates.has_more"
          @click="
            sourceOffset += 50;
            load();
          "
          >下一页</el-button
        >
      </div>
    </section>
    <section v-if="chosen" class="panel" v-loading="locationsLoading">
      <h3>填写移库申请</h3>
      <p>
        {{ chosen.name }} · {{ chosen.lot_no }} · 来源
        {{ chosen.warehouse_name }} / {{ chosen.location_code }}
      </p>
      <p v-if="!locationsLoading && !locations.length" class="form-note">
        该仓尚无其他库位，请先在“资料 → 仓库与库位”新增目标库位。
      </p>
      <form class="business-form" @submit.prevent="create">
        <div class="form-grid">
          <label
            >目标库位<select
              v-model="destination"
              required
              :disabled="busy || locationsLoading"
            >
              <option value="">请选择同仓目标库位</option>
              <option v-for="l in locations" :key="l.id" :value="l.id">
                {{ l.code }} · {{ l.name }}
              </option>
            </select></label
          >
          <label
            >移动数量<input
              v-model="qty"
              required
              inputmode="decimal"
              pattern="[0-9]+([.][0-9]{1,6})?"
              maxlength="21"
              :disabled="busy"
          /></label>
          <label
            >实物批号<input
              v-model="lot"
              required
              maxlength="200"
              :disabled="busy"
          /></label>
        </div>
        <label
          >移库原因<textarea
            v-model="reason"
            required
            maxlength="200"
            :disabled="busy"
          />
        </label>
        <div class="form-actions">
          <el-button
            type="primary"
            native-type="submit"
            :loading="busy"
            :disabled="locationsLoading || !destination"
            >保存移库申请</el-button
          >
        </div>
      </form>
    </section>
  </template>
  <el-dialog
    :model-value="!!mode"
    :title="titles[mode]"
    width="560px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    @close="mode = ''"
  >
    <form class="business-form" @submit.prevent="submit">
      <label v-if="mode === 'review'"
        >审核决定<select v-model="decision">
          <option value="APPROVE">通过</option>
          <option value="REJECT">拒绝</option>
        </select></label
      >
      <template v-if="mode === 'post' && detail"
        ><p>
          {{ detail.document.name }} · {{ quantity(detail.document.qty) }}
          {{ detail.document.base_unit }}<br />{{
            detail.document.source_location
          }}
          → {{ detail.document.destination_location }}
        </p>
        <label
          >核对实物批号<input
            v-model="actualLot"
            required
            maxlength="200" /></label
        ><label
          ><input
            v-model="confirmed"
            type="checkbox"
            required
          />我已核对实物已移至目标库位，数量与单据一致</label
        ></template
      >
      <label
        >操作说明<textarea v-model="actionReason" required maxlength="200" />
      </label>
      <div class="form-actions">
        <el-button native-type="submit" type="primary" :loading="busy"
          >确认保存</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
