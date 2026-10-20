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
import { onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, quantity, useCommand, type PageData } from "./business";
import {
  qualityStates,
  dispositionKinds,
  stockQuality,
  type Disposition,
} from "./quality";
import QualityDetail from "./QualityDetail.vue";
interface Candidate {
  id: string;
  name: string;
  sku_code: string;
  lot_no: string;
  warehouse_name: string;
  location_code: string;
  quality_status: string;
  available: string;
  base_unit: string;
}
const props = defineProps<{ initialId?: string; initialEntityId?: string }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(props.initialEntityId || activeEntity.value),
  selected = ref(props.initialId || ""),
  creating = ref(false);
const documents = ref<PageData<Disposition>>({ items: [], has_more: false });
const candidates = ref<PageData<Candidate>>({ items: [], has_more: false });
const chosen = ref<Candidate | null>(null),
  search = ref(""),
  offset = ref(0),
  sourceOffset = ref(0),
  loading = ref(false);
const kind = ref("RELEASE"),
  qty = ref(""),
  lot = ref(""),
  reason = ref(""),
  evidence = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  operation = "";
onUnmounted(() => epoch++);
const listError = ref(false);
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    selected.value = "";
    creating.value = false;
    chosen.value = null;
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<PageData<Disposition>>(
      `/api/v1/quality-dispositions?${documentQuery(entity.value, offset.value, filters.value)}`,
    );
    if (current === epoch) {
      documents.value = result;
      listError.value = false;
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
async function sources(reset = false) {
  if (reset) sourceOffset.value = 0;
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<PageData<Candidate>>(
      `/api/v1/quality-dispositions/candidates?entity_id=${entity.value}&search=${encodeURIComponent(search.value)}&offset=${sourceOffset.value}`,
    );
    if (current === epoch) candidates.value = result;
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
  qty.value = "";
  lot.value = "";
  reason.value = "";
  evidence.value = "";
  kind.value = "RELEASE";
  operation = crypto.randomUUID();
  sources(true);
}
function choose(row: Candidate) {
  chosen.value = row;
  qty.value = "";
  lot.value = "";
  kind.value = row.quality_status === "UNQUALIFIED" ? "WRITE_OFF" : "RELEASE";
}
async function create() {
  if (!chosen.value) return;
  await execute<{ id: string }>(
    "/api/v1/quality-dispositions",
    {
      client_operation_id: operation,
      entity_id: entity.value,
      balance_id: chosen.value.id,
      kind: kind.value,
      qty: qty.value,
      lot_no: lot.value,
      reason: reason.value,
      evidence: evidence.value,
    },
    (data) => {
      creating.value = false;
      selected.value = data.id;
    },
  );
}
onMounted(() => {
  if (!selected.value) load();
});
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: creating.value, busy: busy.value }),
    "quality",
  ),
);
</script>
<template>
  <QualityDetail
    v-if="selected"
    :key="selected"
    :id="selected"
    @failure="emit('failure', $event)"
    @back="
      selected = '';
      load();
    "
  />
  <template v-else>
    <div class="business-toolbar">
      <el-button
        :disabled="busy || loading"
        @click="creating ? sources() : load()"
        >刷新质量处置</el-button
      >
      <el-button
        v-if="!creating && can('quality.write')"
        type="primary"
        :disabled="loading"
        @click="begin"
        >新建质量处置</el-button
      >
      <el-button
        v-if="creating"
        :disabled="busy"
        @click="
          creating = false;
          load();
        "
        >返回处置列表</el-button
      >
    </div>
    <section v-if="!creating" class="panel" v-loading="loading">
      <h3>质量处置单</h3>
      <p class="form-note">
        自有隔离库存的合格放行与报损出库。每单一个库位批次，支持部分数量处置。
      </p>
      <DocumentFilters
        :applied="filters"
        :states="qualityStates"
        placeholder="单号、商品、批号、仓库或原因"
        :disabled="loading || busy"
        advanced-state
        :kinds="dispositionKinds"
        @apply="applyFilters"
      />
      <p v-if="listError" role="alert" class="form-note">
        查询失败，当前保留上次成功结果；请重试查询。
      </p>
      <el-table :data="documents.items" empty-text="暂无质量处置单">
        <el-table-column label="单号" min-width="210"
          ><template #default="s"
            ><el-button link type="primary" @click="selected = s.row.id">{{
              s.row.number
            }}</el-button></template
          ></el-table-column
        >
        <el-table-column prop="name" label="商品" min-width="180" />
        <el-table-column prop="lot_no" label="批号" min-width="150" />
        <el-table-column label="处置方式" width="110"
          ><template #default="s">{{
            dispositionKinds[s.row.kind]
          }}</template></el-table-column
        >
        <el-table-column label="数量" width="100"
          ><template #default="s"
            >{{ quantity(s.row.qty) }} {{ s.row.base_unit }}</template
          ></el-table-column
        >
        <el-table-column label="状态" width="110"
          ><template #default="s">{{
            qualityStates[s.row.state]
          }}</template></el-table-column
        >
      </el-table>
      <div class="business-toolbar">
        <el-button
          :disabled="loading || offset === 0"
          @click="
            offset -= 50;
            load();
          "
          >上一页</el-button
        ><span>第 {{ offset / 50 + 1 }} 页</span
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
    <section v-else class="panel" v-loading="loading">
      <h3>选择待处置库存</h3>
      <p class="form-note">
        仅列出自有普通隔离或不合格库存。退货待验保管实物请在“退货与退款”处理。
      </p>
      <form class="business-toolbar" @submit.prevent="sources(true)">
        <label
          >检索批次<input
            v-model="search"
            placeholder="商品、编码或批号"
            maxlength="100"
            :disabled="busy || loading" /></label
        ><el-button native-type="submit" :disabled="busy || loading"
          >查询待处置库存</el-button
        >
      </form>
      <el-table
        :data="candidates.items"
        empty-text="暂无符合条件的自有隔离或不合格库存"
      >
        <el-table-column prop="name" label="商品" min-width="170" />
        <el-table-column prop="lot_no" label="批号" min-width="150" />
        <el-table-column label="仓库 / 库位" min-width="150"
          ><template #default="s"
            >{{ s.row.warehouse_name }} / {{ s.row.location_code }}</template
          ></el-table-column
        >
        <el-table-column label="质量" width="110"
          ><template #default="s">{{
            stockQuality[s.row.quality_status]
          }}</template></el-table-column
        >
        <el-table-column label="未占用数量" width="130"
          ><template #default="s"
            >{{ quantity(s.row.available) }} {{ s.row.base_unit }}</template
          ></el-table-column
        >
        <el-table-column label="操作" width="90"
          ><template #default="s"
            ><el-button
              link
              type="primary"
              :disabled="busy"
              @click="choose(s.row)"
              >{{ chosen?.id === s.row.id ? "已选择" : "选择" }}</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <div class="business-toolbar">
        <el-button
          :disabled="busy || loading || sourceOffset === 0"
          @click="
            sourceOffset -= 50;
            sources();
          "
          >上一页批次</el-button
        ><span>第 {{ sourceOffset / 50 + 1 }} 页</span
        ><el-button
          :disabled="busy || loading || !candidates.has_more"
          @click="
            sourceOffset += 50;
            sources();
          "
          >下一页批次</el-button
        >
      </div>
      <form v-if="chosen" class="business-form" @submit.prevent="create">
        <h4>
          已选：{{ chosen.name }} · {{ chosen.lot_no }} ·
          {{ chosen.warehouse_name }} / {{ chosen.location_code }}
        </h4>
        <div class="form-grid">
          <label
            >处置方式<select
              v-model="kind"
              aria-label="处置方式"
              :disabled="busy"
            >
              <option
                v-if="chosen.quality_status === 'QUARANTINE'"
                value="RELEASE"
              >
                合格放行
              </option>
              <option value="WRITE_OFF">报损出库</option>
            </select></label
          >
          <label
            >处置数量（{{ chosen.base_unit }}）<input
              v-model="qty"
              aria-label="处置数量"
              required
              inputmode="decimal"
              maxlength="22"
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
        <p class="form-note">
          {{
            kind === "RELEASE"
              ? "复验合格后转入原库位合格库存；过期、资质无效或整批冻结的库存不能放行。"
              : "按实际处置数量申请报损。过账按当时批次成本扣减，不产生收付款。"
          }}
        </p>
        <label
          >申请原因<input
            v-model="reason"
            required
            maxlength="200"
            :disabled="busy"
        /></label>
        <label
          >复验 / 处置依据<input
            v-model="evidence"
            required
            maxlength="200"
            placeholder="填写复验结论及记录编号，或报损处置记录编号"
            :disabled="busy"
        /></label>
        <div class="business-toolbar">
          <el-button
            native-type="submit"
            type="primary"
            :loading="busy"
            :disabled="loading"
            >提交处置申请</el-button
          >
        </div>
      </form>
    </section>
  </template>
</template>
