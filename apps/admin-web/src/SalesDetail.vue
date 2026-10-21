<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import SaleStockPanel from "./SaleStockPanel.vue";
import { createEditor } from "./workspace";
import SalesHistory from "./SalesHistory.vue";
import AdmissionWarnings from "./AdmissionWarnings.vue";
import DocumentLineGrid from "./DocumentLineGrid.vue";
import ReplenishmentPanel from "./ReplenishmentPanel.vue";
import {
  computed,
  defineAsyncComponent,
  onMounted,
  onUnmounted,
  ref,
} from "vue";
import { request, session } from "./api";
import { can, positive, quantity, useCommand } from "./business";
import {
  localTime,
  salesLabels,
  type SalesDetailData,
  type Shipment,
} from "./sales";
const ReturnsPage = defineAsyncComponent(() => import("./ReturnsPage.vue"));
const returnSource = ref("");
const props = defineProps<{ id: string }>();
const emit = defineEmits<{
  identity: [number: string];
  failure: [error: unknown];
  back: [];
  purchase: [source: { id: string; entity_id: string; sale_id: string }];
}>();
const replenishing = ref(false);
const data = ref<SalesDetailData | null>(null),
  loading = ref(false);
const revising = ref(false);
async function revise() {
  const record = data.value;
  if (!record) return;
  revising.value = true;
  try {
    await createEditor(
      "sale",
      record.order.entity_id,
      {
        warehouse_id: record.order.warehouse_id,
        customer_id: record.order.customer_id,
        delivery_address: record.order.delivery_address,
        minimum_shelf_days: String(record.order.minimum_shelf_days),
        credit_days: String(record.order.credit_days),
        lines: record.lines.map((item) => ({
          client_key: crypto.randomUUID(),
          sku_id: item.sku_id,
          packaging_id: item.package_snapshot?.packaging_id || "",
          qty: item.trade_qty || item.qty,
          unit_price: item.unit_price,
          tax_rate: String(Number(item.tax_rate)),
        })),
      },
      { id: record.order.id, version: record.order.version },
      record.order.number + " 修改",
    );
  } catch (e) {
    emit("failure", e);
  } finally {
    revising.value = false;
  }
}
const mode = ref(""),
  reason = ref(""),
  note = ref(""),
  shippingRef = ref("");
const shipment = ref<Shipment | null>(null);
const allocation = ref<{ order_line_id: string; qty: string }[]>([]);
const checks = ref<{ shipment_line_id: string; qty: string; lot_no: string }[]>(
  [],
);
let operation = "",
  epoch = 0;
const { busy, execute } = useCommand((error) => emit("failure", error));
const open = computed({
  get: () => !!mode.value,
  set: (value: boolean) => {
    if (!value) mode.value = "";
  },
});
const titles: Record<string, string> = {
  approve: "审核销售单",
  close: "关闭订单余量",
  allocate: "分配发货批次",
  pick: "登记拣货",
  verify: "出库复核",
  cancel: "取消发货子单",
  post: "确认销售出库",
};
const hasActive = computed(() =>
  data.value?.shipments.some((s) => !["POSTED", "CANCELLED"].includes(s.state)),
);
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<SalesDetailData>(
      `/api/v1/sales-orders/${props.id}`,
    );
    if (current === epoch) {
      data.value = result;
      emit("identity", result.order.number);
    }
  } catch (error) {
    if (current === epoch) emit("failure", error);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin(action: string, target: Shipment | null = null) {
  shipment.value = target;
  reason.value = "";
  note.value = "";
  shippingRef.value = "";
  operation = crypto.randomUUID();
  allocation.value =
    data.value?.lines.map((line) => ({ order_line_id: line.id, qty: "0" })) ||
    [];
  checks.value =
    target?.lines.map((line) => ({
      shipment_line_id: line.id,
      qty: "",
      lot_no: "",
    })) || [];
  mode.value = action;
}
async function submit() {
  if (!data.value) return;
  let path = `/api/v1/sales-orders/${props.id}/${mode.value}`;
  let payload: unknown = {
    expected_version: data.value.order.version,
    reason: reason.value,
  };
  if (mode.value === "allocate") {
    path = `/api/v1/sales-orders/${props.id}/shipments`;
    payload = {
      expected_version: data.value.order.version,
      client_operation_id: operation,
      lines: allocation.value.filter((line) => positive(line.qty)),
    };
  } else if (shipment.value) {
    path = `/api/v1/shipments/${shipment.value.id}/${mode.value}`;
    payload =
      mode.value === "cancel"
        ? { expected_version: shipment.value.version, reason: reason.value }
        : mode.value === "post"
          ? { expected_version: shipment.value.version }
          : {
              expected_version: shipment.value.version,
              note: note.value,
              lines: checks.value,
              ...(mode.value === "verify"
                ? { shipping_ref: shippingRef.value }
                : {}),
            };
  }
  await execute(path, payload, async () => {
    mode.value = "";
    await load();
  });
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!mode.value || !!returnSource.value, busy: busy.value }),
    "selling",
  ),
);
</script>
<template>
  <ReturnsPage
    v-if="returnSource && data"
    :key="returnSource"
    :initial-source-id="returnSource"
    :initial-entity-id="data.order.entity_id"
    embedded
    @failure="emit('failure', $event)"
    @back="
      returnSource = '';
      load();
    "
  />
  <div v-else class="sales-order" v-loading="loading">
    <div class="business-toolbar">
      <el-button :disabled="busy" @click="emit('back')">返回销售列表</el-button
      ><el-button :disabled="busy" @click="load">刷新销售详情</el-button>
    </div>
    <template v-if="data">
      <AdmissionWarnings :lines="data.lines" />
      <section class="panel">
        <div class="panel-heading">
          <div>
            <span class="eyebrow">SALES ORDER</span>
            <h3 class="document-number">{{ data.order.number }}</h3>
          </div>
          <el-tag>{{ salesLabels[data.order.state] }}</el-tag>
        </div>
        <dl class="facts">
          <dt>销售版本</dt>
          <dd>第 {{ data.order.revision_no }} 版</dd>
          <dt>客户 / 仓库</dt>
          <dd>
            {{ data.order.customer_name }} / {{ data.order.warehouse_name }}
          </dd>
          <dt>收货地址</dt>
          <dd>{{ data.order.delivery_address }}</dd>
          <dt>订单含税金额</dt>
          <dd>{{ data.order.gross_amount }} 元</dd>
          <dt>未出库额度占用</dt>
          <dd>{{ data.order.remaining_exposure }} 元</dd>
          <dt>发货约定</dt>
          <dd>
            最低剩余效期 {{ data.order.minimum_shelf_days }} 天，账期
            {{ data.order.credit_days }} 天
          </dd>
        </dl>
        <DocumentLineGrid
          :entity="data.order.entity_id"
          stage="sales_orders"
          mode="view"
          title="销售订单明细"
          :rows="data.lines"
        />
        <el-alert
          v-if="data.order.state === 'DRAFT' && data.order.revision_no > 1"
          title="销售内容已修改，原信用占用已释放，需重新独立审核后发货。"
          type="info"
          :closable="false"
        />
        <div class="form-actions">
          <el-button
            v-if="can('purchase.read')"
            :disabled="busy"
            @click="replenishing = !replenishing"
            >{{ replenishing ? "收起补货信息" : "缺货与补货" }}</el-button
          >
          <el-button
            v-if="data.order.can_revise && can('sales.write')"
            :disabled="busy"
            @click="revise"
            :loading="revising"
            >修改销售单</el-button
          >
          <el-button
            v-if="data.order.state === 'DRAFT' && can('sales.approve')"
            :disabled="
              busy ||
              data.order.created_by === session?.principal_id ||
              data.order.revision_editors.includes(session?.principal_id || '')
            "
            @click="begin('approve')"
            >审核销售单</el-button
          >
          <el-button
            v-if="data.order.state === 'APPROVED' && can('sales.allocate')"
            type="primary"
            :disabled="
              busy || !data.lines.some((l) => positive(l.remaining_qty))
            "
            @click="begin('allocate')"
            >分配发货批次</el-button
          >
          <el-button
            v-if="
              ['DRAFT', 'APPROVED'].includes(data.order.state) &&
              can('sales.write')
            "
            :disabled="busy || hasActive"
            @click="begin('close')"
            >关闭订单余量</el-button
          >
        </div>
        <p class="form-note">
          制单人及本轮修改人不能审核本单。关闭余量前需取消未出库子单；已出库记录和应收保留。未分配数量仅在待发齐状态可继续发货。
        </p>
      </section>
      <ReplenishmentPanel
        v-if="replenishing && can('purchase.read')"
        :key="data.order.version"
        :id="id"
        @failure="emit('failure', $event)"
        @purchase="emit('purchase', $event)"
      />
      <section
        v-for="sh in data.shipments"
        :key="sh.id"
        class="panel shipment-card"
        :data-testid="`shipment-${sh.id}`"
      >
        <div class="panel-heading">
          <div>
            <small>发货子单</small>
            <h3 class="document-number">{{ sh.number }}</h3>
          </div>
          <el-tag :type="sh.state === 'POSTED' ? 'success' : 'info'">{{
            sh.auto_released_at ? "已超时取消" : salesLabels[sh.state]
          }}</el-tag>
        </div>
        <el-alert
          v-if="sh.auto_released_at"
          :title="`待拣货预占已到期，系统于 ${localTime(sh.auto_released_at)} 自动取消本子单并释放库存。原订单保留，可重新分配发货。`"
          type="info"
          :closable="false"
        />
        <p v-if="!['POSTED', 'CANCELLED'].includes(sh.state)" class="form-note">
          占用截止：{{
            localTime(sh.expires_at)
          }}（北京时间）。未拣货的超时占用可自动回收；已拣货或复核的占用须核对实物后手动取消。
        </p>
        <el-alert
          v-if="sh.expired && !['POSTED', 'CANCELLED'].includes(sh.state)"
          :title="
            sh.state === 'ALLOCATED'
              ? '本次预占已超时，等待自动回收或手动取消后重新分配'
              : '本次预占已超时，请核对实物归位后手动取消子单，再重新分配'
          "
          type="warning"
          :closable="false"
        />
        <p v-if="sh.shipping_ref" class="form-note">
          物流 / 交接凭据：{{ sh.shipping_ref }}
        </p>
        <el-table :data="sh.lines">
          <el-table-column
            prop="name"
            label="商品"
            min-width="160"
          /><el-table-column
            prop="lot_no"
            label="批号"
            min-width="130"
          /><el-table-column
            prop="raw_expiry"
            label="有效期至"
            min-width="115"
          /><el-table-column
            prop="location_code"
            label="货位"
            min-width="100"
          />
          <el-table-column label="分配量" min-width="100"
            ><template #default="{ row }"
              >{{ quantity(row.qty) }} {{ row.base_unit }}</template
            ></el-table-column
          >
          <el-table-column label="实拣量" min-width="95"
            ><template #default="{ row }">{{
              row.picked_qty === null ? "—" : quantity(row.picked_qty)
            }}</template></el-table-column
          >
        </el-table>
        <div class="form-actions">
          <el-button
            v-if="sh.state === 'POSTED' && sh.posting_id && can('returns.read')"
            :disabled="busy"
            @click="returnSource = sh.posting_id"
            >本次出库退货与进度</el-button
          >
          <el-button
            v-if="sh.state === 'ALLOCATED' && can('sales.pick')"
            :disabled="busy || sh.expired"
            @click="begin('pick', sh)"
            >登记拣货</el-button
          >
          <el-button
            v-if="sh.state === 'PICKED' && can('sales.verify')"
            :disabled="
              busy || sh.expired || sh.picked_by === session?.principal_id
            "
            @click="begin('verify', sh)"
            >出库复核</el-button
          >
          <el-button
            v-if="sh.state === 'VERIFIED' && can('sales.post')"
            type="primary"
            :disabled="busy || sh.expired"
            @click="begin('post', sh)"
            >销售出库</el-button
          >
          <el-button
            v-if="
              !['POSTED', 'CANCELLED'].includes(sh.state) && can('sales.write')
            "
            :disabled="busy"
            @click="begin('cancel', sh)"
            >取消发货子单</el-button
          >
        </div>
      </section>
      <el-empty
        v-if="!data.shipments.length"
        description="审核后按本次发货数量分配批次，支持分批发货"
      />
      <SalesHistory
        :key="data.order.version"
        :id="id"
        @failure="emit('failure', $event)"
      />
    </template>
  </div>
  <el-dialog
    v-model="open"
    :title="titles[mode] || ''"
    width="780px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="submit">
      <template v-if="mode === 'allocate' && data">
        <SaleStockPanel
          channel="wholesale"
          :entity="data.order.entity_id"
          :warehouse="data.order.warehouse_id"
          :skus="data.lines.map((row) => row.sku_id)"
          :minimum-shelf-days="data.order.minimum_shelf_days"
          :lines="data.lines"
          :disabled="busy"
          @fill="allocation = $event"
        />
        <p class="form-note">
          本次发货数量按基本单位填写。系统优先分配符合最低效期要求的近效期合格批次。未发商品填
          0；分配成功后请在 15 分钟内完成拣货、独立复核和出库。
        </p>
        <div
          v-for="(line, i) in allocation"
          :key="line.order_line_id"
          class="line-editor"
        >
          <b>{{ data.lines[i]?.name }}</b>
          <p class="form-note">
            未分配 {{ quantity(data.lines[i]?.remaining_qty || "0") }}
            {{ data.lines[i]?.base_unit }}
          </p>
          <label
            >本次发货数量<span class="operation-quantity"
              ><input
                v-model="line.qty"
                aria-label="本次发货数量"
                required
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,6})?"
                maxlength="21"
              /><span
                class="operation-unit"
                aria-hidden="true"
                :title="data.lines[i]?.base_unit"
                >{{ data.lines[i]?.base_unit }}</span
              ></span
            ></label
          >
        </div>
      </template>
      <template v-else-if="['pick', 'verify'].includes(mode) && shipment">
        <p class="form-note">
          请按基本单位逐行核对实物批号和数量。复核人必须与拣货人不同；批号或数量不一致时无法提交。
        </p>
        <div
          v-for="(line, i) in checks"
          :key="line.shipment_line_id"
          class="line-editor"
        >
          <b>{{ shipment.lines[i]?.name }}</b>
          <p class="form-note">
            应发批号：{{ shipment.lines[i]?.lot_no }} · 数量：{{
              quantity(shipment.lines[i]?.qty || "0")
            }}
            {{ shipment.lines[i]?.base_unit }} · 货位：{{
              shipment.lines[i]?.location_code
            }}
          </p>
          <div class="form-grid">
            <label
              >实物批号<input
                v-model="line.lot_no"
                required
                maxlength="100"
                autocomplete="off" /></label
            ><label
              >实物数量<span class="operation-quantity"
                ><input
                  v-model="line.qty"
                  aria-label="实物数量"
                  required
                  inputmode="decimal"
                  pattern="[0-9]+([.][0-9]{1,6})?"
                  maxlength="21"
                /><span
                  class="operation-unit"
                  aria-hidden="true"
                  :title="shipment.lines[i]?.base_unit"
                  >{{ shipment.lines[i]?.base_unit }}</span
                ></span
              ></label
            >
          </div>
        </div>
        <label v-if="mode === 'verify'"
          >物流 / 交接凭据<input
            v-model="shippingRef"
            required
            maxlength="120" /></label
        ><label
          >核查说明<textarea v-model="note" required maxlength="500"></textarea>
        </label>
      </template>
      <template v-else-if="mode === 'post' && shipment">
        <el-alert
          title="确认后同步扣减库存和成本，生成客户应收"
          type="warning"
          :closable="false"
        />
        <p>{{ shipment.number }} · {{ shipment.shipping_ref }}</p>
        <div v-for="line in shipment.lines" :key="line.id" class="line-editor">
          <b>{{ line.name }}</b>
          <p>
            {{ line.lot_no }} · {{ quantity(line.qty) }} {{ line.base_unit }}
          </p>
        </div>
        <p class="form-note">
          出库前会再次核查资质、信用、批次效期与冻结状态。当前版本不支持已出库冲销，请确认实物与单据一致。
        </p>
      </template>
      <template v-else
        ><p class="form-note">
          {{
            mode === "approve"
              ? "确认客户和商品资质、价格、账期及收货约定，审核通过后占用客户额度。"
              : "释放尚未完成部分的占用，保留已出库及应收记录。"
          }}
        </p>
        <label
          >操作说明<textarea
            v-model="reason"
            required
            maxlength="500"
          ></textarea></label
      ></template>
      <div class="form-actions">
        <el-button :disabled="busy" @click="open = false">返回</el-button
        ><el-button type="primary" native-type="submit" :loading="busy">{{
          mode === "approve"
            ? "确认审核"
            : mode === "allocate"
              ? "确认分配"
              : mode === "pick"
                ? "保存拣货"
                : mode === "verify"
                  ? "确认复核"
                  : mode === "post"
                    ? "确认出库"
                    : "确认关闭"
        }}</el-button>
      </div>
    </form>
  </el-dialog>
</template>
