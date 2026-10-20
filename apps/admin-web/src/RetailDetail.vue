<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, type Named } from "./api";
import { can, quantity, useCommand } from "./business";
import { localTime } from "./sales";
import { retailStates, type RetailDetailData } from "./retail";
import { canApplyReturn } from "./returns";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{
  back: [];
  returnSale: [source: { posting_id: string; entity_id: string }];
  failure: [error: unknown];
}>();
const data = ref<RetailDetailData | null>(null),
  loading = ref(false);
const accounts = ref<Named[]>([]),
  action = ref(""),
  reason = ref(""),
  confirmed = ref(false);
const account = ref(""),
  tendered = ref(""),
  operation = ref("");
const checks = ref<{ allocation_id: string; qty: string; lot_no: string }[]>(
  [],
);
const dialog = computed({
  get: () => !!action.value,
  set: (value: boolean) => {
    if (!value) action.value = "";
  },
});
const titles: Record<string, string> = {
  cash: "登记实际现金收款",
  complete: "核对实物并完成出库",
  cancel: "取消未收款零售单",
  "refund-pending": "退还现金并取消零售单",
};
const { busy, execute } = useCommand((e) => emit("failure", e));
const changePreview = computed(() => {
  if (!data.value || !/^(0|[1-9]\d{0,17})(\.\d{1,2})?$/.test(tendered.value))
    return "—";
  const cents = (value: string) => {
    const [whole, fraction = ""] = value.split(".");
    return BigInt(whole!) * 100n + BigInt(fraction.padEnd(2, "0"));
  };
  const change = cents(tendered.value) - cents(data.value.sale.gross_amount);
  return change < 0n
    ? "现金不足"
    : `${change / 100n}.${String(change % 100n).padStart(2, "0")} 元`;
});
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<RetailDetailData>(
      "/api/v1/retail/sales/" + props.id,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function begin(name: string) {
  if (!data.value) return;
  reason.value = "";
  confirmed.value = false;
  account.value = "";
  tendered.value = "";
  operation.value = crypto.randomUUID();
  action.value = name;
  checks.value = data.value.allocations.map((r) => ({
    allocation_id: r.id,
    qty: "",
    lot_no: "",
  }));
  if (name === "cash") {
    const current = epoch;
    accounts.value = [];
    try {
      const options = await request<{ accounts: Named[] }>(
        `/api/v1/retail/options?entity_id=${data.value.sale.entity_id}`,
      );
      if (current === epoch) {
        accounts.value = options.accounts;
        account.value = options.accounts[0]?.id || "";
      }
    } catch (e) {
      if (current === epoch) emit("failure", e);
    }
  }
}
function nameOf(id: string) {
  return (
    data.value?.lines.find((r) => r.id === id)?.product_snapshot.name || ""
  );
}
function baseUnitOf(id: string) {
  return (
    data.value?.lines.find((r) => r.id === id)?.package_snapshot.base_unit ||
    "基本单位"
  );
}
async function save() {
  if (!data.value) return;
  const cash = ["cash", "refund-pending"].includes(action.value);
  await execute(
    "/api/v1/retail/sales/" + props.id + "/" + action.value,
    {
      expected_version: data.value.sale.version,
      reason: reason.value,
      ...(cash
        ? { client_operation_id: operation.value, confirmed: confirmed.value }
        : {}),
      ...(action.value === "cash"
        ? { account_id: account.value, tendered: tendered.value }
        : {}),
      ...(action.value === "complete" ? { lines: checks.value } : {}),
    },
    async () => {
      action.value = "";
      await load();
    },
  );
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!action.value, busy: busy.value }),
    "retail",
  ),
);
</script>

<template>
  <section class="panel" v-loading="loading">
    <div class="detail-toolbar">
      <el-button :disabled="busy" @click="emit('back')">返回零售列表</el-button
      ><el-button :disabled="busy" @click="load">刷新零售详情</el-button>
    </div>
    <template v-if="data">
      <div class="panel-heading">
        <h3 class="document-number">{{ data.sale.number }}</h3>
        <el-tag
          :type="data.sale.state === 'PAID_PENDING_POST' ? 'warning' : 'info'"
          >{{
            data.reservation.auto_released_at
              ? "已超时取消"
              : retailStates[data.sale.state]
          }}</el-tag
        >
      </div>
      <el-alert
        v-if="data.reservation.auto_released_at"
        :title="`未付款预占已到期，系统于 ${localTime(data.reservation.auto_released_at)} 自动取消并释放库存。如需交易，请重新开单。`"
        type="info"
        :closable="false"
      />
      <el-alert
        v-if="data.sale.state === 'PAID_PENDING_POST'"
        title="本单已收款，请勿重复向顾客收款"
        description="库存已保护。核对实物后完成出库；若出库失败，可排除原因后重试，或由有权限的人员退还现金并取消本单。"
        type="warning"
        :closable="false"
        show-icon
      />
      <dl class="facts">
        <dt>应收金额</dt>
        <dd>
          <strong>{{ data.sale.gross_amount }} 元</strong>
        </dd>
        <dt>创建时间</dt>
        <dd>{{ localTime(data.sale.created_at) }}</dd>
        <template v-if="data.sale.state === 'RESERVED'"
          ><dt>收款前预占到期</dt>
          <dd>
            {{
              localTime(data.reservation.expires_at)
            }}（超时停止收款，等待自动回收或手动取消后重新开单）
          </dd></template
        >
      </dl>
      <el-table :data="data.lines"
        ><el-table-column label="商品" min-width="180"
          ><template #default="{ row }"
            >{{ row.product_snapshot.name }} ·
            {{ row.product_snapshot.specification }}</template
          ></el-table-column
        ><el-table-column label="交易数量 / 单位" min-width="140"
          ><template #default="{ row }"
            >{{ quantity(row.trade_qty || row.qty) }}
            {{
              row.package_snapshot?.unit_name || row.package_snapshot.base_unit
            }}<small v-if="row.package_snapshot?.packaging_id" class="cell-sub"
              >1 {{ row.package_snapshot.unit_name }} =
              {{ row.package_snapshot.numerator }}/{{
                row.package_snapshot.denominator
              }}
              {{ row.package_snapshot.base_unit }}</small
            ></template
          ></el-table-column
        >
        <el-table-column label="基本单位数量" width="130"
          ><template #default="{ row }"
            >{{ quantity(row.qty) }}
            {{ row.package_snapshot.base_unit }}</template
          ></el-table-column
        ><el-table-column label="交易单位单价（元）" width="150"
          ><template #default="{ row }">{{
            quantity(row.unit_price)
          }}</template></el-table-column
        ><el-table-column prop="gross_amount" label="应收（元）" width="130"
      /></el-table>
      <h3>交付批次</h3>
      <el-table :data="data.allocations"
        ><el-table-column label="商品" min-width="180"
          ><template #default="{ row }">{{
            nameOf(row.source_line_id)
          }}</template></el-table-column
        ><el-table-column
          prop="lot_no"
          label="分配批号"
          min-width="160"
        /><el-table-column label="分配数量" width="130"
          ><template #default="{ row }"
            >{{ quantity(row.allocated_qty) }}
            {{ baseUnitOf(row.source_line_id) }}</template
          ></el-table-column
        ><el-table-column label="有效截止时点" min-width="180"
          ><template #default="{ row }">{{
            localTime(row.expiry_exclusive)
          }}</template></el-table-column
        ></el-table
      >
      <section v-if="data.payments.length" aria-label="零售现金记录">
        <h3>现金记录</h3>
        <el-table :data="data.payments"
          ><el-table-column label="类型" width="100"
            ><template #default="{ row }">{{
              row.direction === "RECEIPT" ? "收款" : "退款"
            }}</template></el-table-column
          ><el-table-column
            prop="amount"
            label="记账金额（元）"
            width="150"
          /><el-table-column
            prop="tendered"
            label="交付现金（元）"
            width="150"
          /><el-table-column
            prop="change_amount"
            label="找零（元）"
            width="130"
          /><el-table-column
            prop="note"
            label="核对说明"
            min-width="190"
          /><el-table-column label="登记时间" min-width="180"
            ><template #default="{ row }">{{
              localTime(row.created_at)
            }}</template></el-table-column
          ></el-table
        >
      </section>
      <div class="form-actions">
        <template v-if="data.sale.state === 'RESERVED' && can('retail.sell')"
          ><el-button :disabled="busy" @click="begin('cancel')"
            >取消零售单</el-button
          ><el-button type="primary" :disabled="busy" @click="begin('cash')"
            >登记现金收款</el-button
          ></template
        >
        <template v-if="data.sale.state === 'PAID_PENDING_POST'"
          ><el-button
            v-if="can('retail.refund')"
            :disabled="busy"
            @click="begin('refund-pending')"
            >登记退款并取消</el-button
          ><el-button
            v-if="can('retail.sell')"
            type="primary"
            :disabled="busy"
            @click="begin('complete')"
            >核对并完成出库</el-button
          ></template
        >
      </div>
      <section v-if="data.sale.state === 'COMPLETED'" aria-label="零售退货汇总">
        <h3>原单退货与退款</h3>
        <dl class="facts">
          <dt>已退货金额</dt>
          <dd>{{ data.returns_summary.returned_amount }} 元</dd>
          <dt>已退现金</dt>
          <dd>{{ data.returns_summary.refunded_amount }} 元</dd>
          <dt>待退现金</dt>
          <dd>{{ data.returns_summary.pending_refund_amount }} 元</dd>
        </dl>
        <p class="form-note">
          支持按原批次分次退货。实物退回后先隔离验收，通过并入库后再登记现金退款。
        </p>
        <el-button
          v-if="canApplyReturn('RETAIL') && data.sale.posting_id"
          :disabled="busy"
          @click="
            emit('returnSale', {
              posting_id: data.sale.posting_id!,
              entity_id: data.sale.entity_id,
            })
          "
          >办理原单退货</el-button
        >
      </section>
    </template>
  </section>
  <el-dialog
    v-model="dialog"
    :title="titles[action]"
    width="740px"
    class="business-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
  >
    <form v-if="data" class="business-form" @submit.prevent="save">
      <fieldset :disabled="busy">
        <p>
          本单金额：<strong>{{ data.sale.gross_amount }} 元</strong>
        </p>
        <template v-if="action === 'cash'"
          ><div class="form-grid">
            <label
              >现金账户<select v-model="account" aria-label="现金账户" required>
                <option value="" disabled>请选择现金账户</option>
                <option v-for="a in accounts" :key="a.id" :value="a.id">
                  {{ a.name }}
                </option>
              </select></label
            ><label
              >顾客交付现金（元）<input
                v-model="tendered"
                inputmode="decimal"
                required
            /></label>
          </div>
          <p class="form-note">
            应找零：{{
              changePreview
            }}。请核对实际现金并找零；遇到网络异常可用同一表单重试或刷新详情核对，勿重复收取现金。
          </p></template
        >
        <template v-if="action === 'refund-pending'"
          ><p class="form-note">
            将本单净收现金
            {{ data.sale.gross_amount }}
            元退还顾客后登记，退款记入原现金账户，同时解除库存预占。
          </p></template
        >
        <template v-if="action === 'complete'">
          <p class="form-note">
            交付核对按商品基本单位填写，请勿将箱数直接填入。
          </p>
          <div
            v-for="(row, i) in data.allocations"
            :key="row.id"
            class="receipt-card"
          >
            <p>
              {{ nameOf(row.source_line_id) }} · 批号 {{ row.lot_no }} · 应交付
              {{ quantity(row.allocated_qty) }}
              {{ baseUnitOf(row.source_line_id) }}
            </p>
            <div class="form-grid">
              <label
                >实物核对数量（基本单位）<input
                  v-model="checks[i]!.qty"
                  :aria-label="'交付数量 ' + (i + 1)"
                  inputmode="decimal"
                  required /></label
              ><label
                >实物批号<input
                  v-model="checks[i]!.lot_no"
                  :aria-label="'交付批号 ' + (i + 1)"
                  required
              /></label>
            </div></div
        ></template>
        <label
          >操作说明<textarea
            v-model="reason"
            maxlength="200"
            required
            rows="2"
          />
        </label>
        <label
          v-if="['cash', 'refund-pending'].includes(action)"
          class="confirmation-check"
          ><input v-model="confirmed" type="checkbox" required />{{
            action === "cash"
              ? "我已核对现金实收与找零"
              : "我已向顾客退还本单实收现金"
          }}</label
        >
        <div class="form-actions">
          <el-button @click="action = ''">返回</el-button
          ><el-button type="primary" native-type="submit" :loading="busy"
            >确认保存</el-button
          >
        </div>
      </fieldset>
    </form>
  </el-dialog>
</template>
