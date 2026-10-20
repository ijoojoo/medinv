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
import DateInput from "./DateInput.vue";
import { businessToday } from "./dates";
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session, type Named } from "./api";
import {
  can,
  loadOptions,
  quantity,
  useCommand,
  type PageData,
} from "./business";
import { localTime } from "./sales";
import OpeningImport from "./OpeningImport.vue";
import type { ImportSku, OpeningInput } from "./openingImport";

interface Opening {
  id: string;
  number: string;
  warehouse_name: string;
  source_ref: string;
  state: string;
  version: string;
  created_by: string;
  reason: string;
  created_at: string;
  reviewed_at: string | null;
  review_note: string | null;
  evidence_ref: string | null;
  cancel_reason: string | null;
}
interface Line {
  id: string;
  name: string;
  lot_no: string;
  location_code: string;
  base_unit: string;
  mfg_date: string;
  expiry_date: string;
  qty: string;
  cost_amount: string;
}
interface Detail {
  document: Opening;
  lines: Line[];
  cost_total: string;
}
interface Options {
  warehouses: Named[];
  locations: { id: string; code: string; warehouse_id: string }[];
  skus: ImportSku[];
}
const money = (value: string) => {
  const [whole, fraction = ""] = value.split(".");
  return `${whole}.${fraction.padEnd(2, "0").slice(0, 2)}`;
};
const props = defineProps<{ initialId?: string; initialEntityId?: string }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(props.initialEntityId || activeEntity.value);
const documents = ref<PageData<Opening>>({ items: [], has_more: false });
const detail = ref<Detail | null>(null),
  creating = ref(false),
  loading = ref(false),
  offset = ref(0);
const options = ref<Options>({ warehouses: [], locations: [], skus: [] });
const importPending = ref(false);
function applyImport(lines: OpeningInput[], filename: string) {
  form.value.lines = lines.map((line) => ({
    ...line,
    key: crypto.randomUUID(),
  }));
  if (!form.value.source_ref) form.value.source_ref = filename.slice(0, 120);
}
const blank = () => ({
  key: crypto.randomUUID(),
  sku_id: "",
  location_id: "",
  lot_no: "",
  mfg_date: "",
  expiry_date: "",
  qty: "",
  cost_amount: "",
});
const form = ref({
  client_operation_id: "",
  warehouse_id: "",
  source_ref: "",
  reason: "",
  lines: [blank()],
});
const locations = computed(() =>
  options.value.locations.filter(
    (l) => l.warehouse_id === form.value.warehouse_id,
  ),
);
const self = computed(
  () => detail.value?.document.created_by === session.value?.principal_id,
);
const states: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "待启用",
  REJECTED: "已拒绝",
  CANCELLED: "已取消",
  POSTED: "已启用",
};
const action = ref(""),
  decision = ref("APPROVE"),
  reason = ref(""),
  evidence = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0;
onUnmounted(() => epoch++);
const listError = ref(false);
async function load(reset = false) {
  if (reset) {
    offset.value = 0;
    detail.value = null;
    creating.value = false;
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const data = await request<PageData<Opening>>(
      `/api/v1/inventory-openings?${documentQuery(entity.value, offset.value, filters.value)}`,
    );
    if (current === epoch) {
      documents.value = data;
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
async function show(id: string) {
  const current = ++epoch;
  loading.value = true;
  try {
    const data = await request<Detail>(`/api/v1/inventory-openings/${id}`);
    if (current === epoch) {
      detail.value = data;
      creating.value = false;
    }
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function warehouseChanged() {
  for (const line of form.value.lines)
    line.location_id = locations.value[0]?.id || "";
}
function add() {
  form.value.lines.push({
    ...blank(),
    location_id: locations.value[0]?.id || "",
  });
}
async function begin() {
  const current = ++epoch;
  loading.value = true;
  try {
    const data = await loadOptions<Options>(
      `/api/v1/inventory-openings/options?entity_id=${entity.value}`,
    );
    if (current !== epoch) return;
    options.value = data;
    form.value = {
      client_operation_id: crypto.randomUUID(),
      warehouse_id: data.warehouses[0]?.id || "",
      source_ref: "",
      reason: "",
      lines: [blank()],
    };
    warehouseChanged();
    creating.value = true;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function create() {
  if (importPending.value) return;
  await execute<{ id: string }>(
    "/api/v1/inventory-openings",
    {
      ...form.value,
      entity_id: entity.value,
      lines: form.value.lines.map(({ key: _key, ...line }) => line),
    },
    async (result) => {
      await show(result.id);
    },
  );
}
function open(next: string) {
  action.value = next;
  reason.value = "";
  evidence.value = "";
  decision.value = "APPROVE";
}
async function transition() {
  if (!detail.value) return;
  const id = detail.value.document.id;
  await execute(
    `/api/v1/inventory-openings/${id}/${action.value}`,
    {
      expected_version: detail.value.document.version,
      reason: reason.value,
      ...(action.value === "review"
        ? { decision: decision.value, evidence_ref: evidence.value }
        : {}),
    },
    async () => {
      action.value = "";
      await show(id);
    },
  );
}
onMounted(() => (props.initialId ? show(props.initialId) : load()));
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: creating.value || !!action.value, busy: busy.value }),
    "openings",
  ),
);
</script>

<template>
  <section class="panel" v-loading="loading">
    <template v-if="detail">
      <div class="detail-toolbar">
        <el-button :disabled="busy" @click="load(true)"
          >← 返回期初列表</el-button
        >
        <el-button :disabled="busy" @click="show(detail.document.id)"
          >刷新期初单</el-button
        >
      </div>
      <div class="panel-heading">
        <div>
          <h3>期初库存详情</h3>
          <span class="mono document-number">{{ detail.document.number }}</span>
        </div>
        <el-tag>{{ states[detail.document.state] }}</el-tag>
      </div>
      <dl class="facts">
        <dt>启用仓库</dt>
        <dd>{{ detail.document.warehouse_name }}</dd>
        <dt>期初清单凭据</dt>
        <dd>{{ detail.document.source_ref }}</dd>
        <dt>期初成本合计</dt>
        <dd>{{ money(detail.cost_total) }} 元</dd>
        <dt>说明</dt>
        <dd>{{ detail.document.reason }}</dd>
        <dt>创建时间</dt>
        <dd>{{ localTime(detail.document.created_at) }}</dd>
      </dl>
      <el-alert
        v-if="detail.document.state === 'POSTED'"
        title="期初库存已启用，可用于正常销售、调拨和盘点。"
        type="success"
        :closable="false"
      />
      <el-table :data="detail.lines" data-testid="opening-lines">
        <el-table-column prop="name" label="商品" min-width="150" />
        <el-table-column prop="location_code" label="库位" min-width="100" />
        <el-table-column prop="lot_no" label="批号" min-width="130" />
        <el-table-column prop="mfg_date" label="生产日期" width="115" />
        <el-table-column prop="expiry_date" label="有效期至" width="115" />
        <el-table-column label="基本数量" min-width="110"
          ><template #default="{ row }"
            >{{ quantity(row.qty) }} {{ row.base_unit }}</template
          ></el-table-column
        >
        <el-table-column label="账面成本（元）" min-width="130"
          ><template #default="{ row }">{{
            money(row.cost_amount)
          }}</template></el-table-column
        >
      </el-table>
      <p v-if="detail.document.reviewed_at" class="form-note">
        审核时间 {{ localTime(detail.document.reviewed_at) }} · 凭据
        {{ detail.document.evidence_ref }} · {{ detail.document.review_note }}
      </p>
      <p v-if="detail.document.cancel_reason" class="form-note">
        取消原因：{{ detail.document.cancel_reason }}
      </p>
      <div class="form-actions">
        <span
          v-if="self && ['DRAFT', 'APPROVED'].includes(detail.document.state)"
          class="form-note"
          >制单人不能审核或启用本单，请交由另一位有权限的成员核对。</span
        >
        <el-button
          v-if="
            can('opening.write') &&
            ['DRAFT', 'APPROVED'].includes(detail.document.state)
          "
          :disabled="busy"
          @click="open('cancel')"
          >取消期初单</el-button
        >
        <el-button
          v-if="can('opening.review') && detail.document.state === 'DRAFT'"
          type="primary"
          :disabled="busy || self"
          @click="open('review')"
          >审核期初单</el-button
        >
        <el-button
          v-if="can('opening.post') && detail.document.state === 'APPROVED'"
          type="primary"
          :disabled="busy || self"
          @click="open('post')"
          >启用期初库存</el-button
        >
      </div>
    </template>
    <template v-else>
      <div class="business-toolbar">
        <el-button v-if="!creating" @click="load()">刷新期初列表</el-button>
        <el-button
          v-if="!creating && can('opening.write')"
          type="primary"
          @click="begin"
          >新建期初库存</el-button
        >
        <el-button v-if="creating" :disabled="busy" @click="load(true)"
          >返回期初列表</el-button
        >
      </div>
      <p class="form-note">
        首次启用仓库时，按实物清单登记原有合格库存及账面成本。每个仓库仅可启用一次；已有到货或库存业务的仓库不再开放期初。
      </p>
      <template v-if="!creating">
        <DocumentFilters
          :applied="filters"
          :states="states"
          placeholder="单号、仓库、清单凭据或原因"
          :disabled="loading || busy"
          advanced-state
          @apply="applyFilters"
        />
        <p v-if="listError" role="alert" class="form-note">
          查询失败，当前保留上次成功结果；请重试查询。
        </p>
        <el-table :data="documents.items" empty-text="暂无期初单">
          <el-table-column prop="number" label="期初单号" min-width="260" />
          <el-table-column prop="warehouse_name" label="仓库" min-width="150" />
          <el-table-column label="状态" width="110"
            ><template #default="{ row }">{{
              states[row.state]
            }}</template></el-table-column
          >
          <el-table-column prop="source_ref" label="清单凭据" min-width="150" />
          <el-table-column label="操作" width="110"
            ><template #default="{ row }"
              ><el-button link type="primary" @click="show(row.id)"
                >查看期初单</el-button
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
            :disabled="!documents.has_more || loading"
            @click="
              offset += 50;
              load();
            "
            >下一页</el-button
          >
        </div>
      </template>
      <form v-else class="business-form" @submit.prevent="create">
        <el-alert
          v-if="!options.warehouses.length"
          title="暂无可开账仓库，请先在“资料 → 仓库与库位”建立尚未使用的实体仓库。"
          type="info"
          :closable="false"
        />
        <p>
          请一次录入该仓库完整期初清单（当前最多 200
          行），数量按商品基本单位填写，成本填写该行总额。保存后交独立成员核对；录入错误可取消并重建。
        </p>
        <div class="form-grid">
          <label
            >启用仓库<select
              v-model="form.warehouse_id"
              required
              @change="warehouseChanged"
            >
              <option v-for="w in options.warehouses" :key="w.id" :value="w.id">
                {{ w.name }}
              </option>
            </select></label
          >
          <label
            >期初清单凭据<input
              v-model="form.source_ref"
              required
              maxlength="120"
          /></label>
        </div>
        <OpeningImport
          :key="`${entity}:${form.warehouse_id}`"
          :warehouse="form.warehouse_id"
          :skus="options.skus"
          :locations="options.locations"
          :disabled="busy"
          @apply="applyImport"
          @pending="importPending = $event"
        />
        <div
          v-for="(line, index) in form.lines"
          :key="line.key"
          class="line-editor"
        >
          <div class="line-heading">
            <b>期初行 {{ index + 1 }}</b
            ><el-button
              link
              :disabled="busy || form.lines.length === 1"
              @click="form.lines.splice(index, 1)"
              >移除此行</el-button
            >
          </div>
          <div class="form-grid">
            <label
              >期初商品<select v-model="line.sku_id" required>
                <option disabled value="">请选择已审核商品</option>
                <option v-for="s in options.skus" :key="s.id" :value="s.id">
                  {{ s.name }} · {{ s.code }} / {{ s.base_unit }}
                </option>
              </select></label
            >
            <label
              >期初库位<select v-model="line.location_id" required>
                <option v-for="l in locations" :key="l.id" :value="l.id">
                  {{ l.code }}
                </option>
              </select></label
            >
            <label
              >生产批号<input v-model="line.lot_no" required maxlength="100"
            /></label>
            <label
              >基本单位数量<input
                v-model="line.qty"
                required
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,6})?"
                maxlength="21"
            /></label>
            <label
              >生产日期<DateInput
                v-model="line.mfg_date"
                :max="businessToday()"
                required
            /></label>
            <label
              >有效期至<DateInput
                v-model="line.expiry_date"
                required
                :min="businessToday()"
                max="9999-12-30"
            /></label>
            <label
              >账面成本总额（元）<input
                v-model="line.cost_amount"
                required
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,2})?"
                maxlength="19"
            /></label>
          </div>
        </div>
        <el-button :disabled="busy || form.lines.length >= 200" @click="add"
          >添加期初行</el-button
        >
        <label
          >开账说明<input v-model="form.reason" required maxlength="200"
        /></label>
        <div class="form-actions">
          <el-button
            native-type="submit"
            type="primary"
            :loading="busy"
            :disabled="!form.warehouse_id || importPending"
            >保存期初清单</el-button
          >
        </div>
      </form>
    </template>
  </section>
  <el-dialog
    :model-value="!!action"
    :title="
      action === 'review'
        ? '审核期初单'
        : action === 'post'
          ? '启用期初库存'
          : '取消期初单'
    "
    width="600px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    @close="action = ''"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="transition">
      <template v-if="action === 'review'">
        <p>请核对完整实物清单、批号效期和账面成本。</p>
        <label
          >审核结论<select v-model="decision">
            <option value="APPROVE">审核通过</option>
            <option value="REJECT">拒绝</option>
          </select></label
        >
        <label
          >核验凭据<input v-model="evidence" required maxlength="120"
        /></label>
      </template>
      <template v-if="action === 'post'"
        ><p>
          将 {{ detail?.document.warehouse_name }} 的
          {{ detail?.lines.length }} 行期初库存正式启用，成本合计
          {{ money(detail?.cost_total || "0") }} 元。
        </p>
        <p class="form-note">
          启用当天登记库存和成本流水，不产生采购应付。启用后不能重复开账或直接修改本单，请确认清单完整准确。
        </p></template
      >
      <label>操作说明<input v-model="reason" required maxlength="200" /></label>
      <div class="form-actions">
        <el-button native-type="submit" type="primary" :loading="busy">{{
          action === "review"
            ? "提交审核"
            : action === "post"
              ? "确认启用"
              : "确认取消"
        }}</el-button>
      </div>
    </form>
  </el-dialog>
</template>
