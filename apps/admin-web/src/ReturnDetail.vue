<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { can, quantity, useCommand } from "./business";
import { localTime } from "./sales";
import {
  returnKinds,
  canApplyReturn,
  canHandleReturnCustody,
  returnStates,
  type ReturnDocument,
  type ReturnLine,
} from "./returns";
import RetailReturnRefund from "./RetailReturnRefund.vue";
import ReturnRefunds from "./ReturnRefunds.vue";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ failure: [error: unknown]; back: [] }>();
const data = ref<{
  document: ReturnDocument;
  lines: ReturnLine[];
  allocations: { id: string; cash_number: string; amount: string }[];
  allocations_has_more: boolean;
  result: { cost_amount: string; purchase_cost_variance: string } | null;
  custody: {
    zone_label: string;
    created_at: string;
    note: string;
    outcome: "ACCEPTED" | "HANDED_BACK" | null;
    close_note: string | null;
  }[];
} | null>(null);
const zone = ref(""),
  confirmed = ref(false);
const needsCustody = computed(
  () =>
    ["SALES", "RETAIL"].includes(data.value?.document.kind || "") &&
    data.value?.document.workflow_version === 2,
);
const holding = computed(
  () => !!data.value?.custody.length && !data.value.custody[0].outcome,
);
const offset = ref(0),
  loading = ref(false),
  mode = ref(""),
  reason = ref(""),
  target = ref(""),
  decision = ref("APPROVE");
const checks = ref<
  {
    source_posting_line_id: string;
    qty: string;
    lot_no: string;
    name: string;
  }[]
>([]);
const open = computed({
  get: () => !!mode.value,
  set: (v: boolean) => {
    if (!v) mode.value = "";
  },
});
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0,
  operation = "";
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<typeof data.value>(
      "/api/v1/returns/" + props.id + "?offset=" + offset.value,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin(value: string, id = "") {
  mode.value = value;
  reason.value = "";
  target.value = id;
  operation = crypto.randomUUID();
  decision.value = "APPROVE";
  zone.value = "";
  confirmed.value = false;
  if (["review", "receive"].includes(value))
    checks.value = (data.value?.lines || []).map((r) => ({
      source_posting_line_id: r.id,
      qty: "",
      lot_no: "",
      name: r.name,
    }));
}
async function submit() {
  if (!data.value) return;
  const payload =
    mode.value === "reverse"
      ? { client_operation_id: operation, reason: reason.value }
      : mode.value === "review"
        ? {
            expected_version: data.value.document.version,
            reason: reason.value,
            decision: decision.value,
            lines:
              decision.value === "REJECT"
                ? []
                : checks.value.map(({ name, ...r }) => r),
          }
        : {
            expected_version: data.value.document.version,
            reason: reason.value,
          };
  if (mode.value === "receive")
    Object.assign(payload, {
      zone_label: zone.value,
      confirmed: confirmed.value,
      lines: checks.value.map(({ name, ...r }) => r),
    });
  if (mode.value === "hand-back")
    Object.assign(payload, { confirmed: confirmed.value });
  const path =
    mode.value === "reverse"
      ? "/api/v1/settlements/allocations/" + target.value + "/reverse"
      : "/api/v1/returns/" + props.id + "/" + mode.value;
  await execute(path, payload, async () => {
    open.value = false;
    await load();
  });
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!mode.value, busy: busy.value }),
    "returns",
  ),
);
</script>
<template>
  <div v-loading="loading">
    <div class="business-toolbar">
      <el-button @click="emit('back')" :disabled="busy">返回退货列表</el-button
      ><el-button @click="load" :disabled="busy">刷新退货详情</el-button>
    </div>
    <section v-if="data" class="panel">
      <div class="panel-heading">
        <h3 class="document-number">{{ data.document.number }}</h3>
        <el-tag>{{ returnStates[data.document.state] }}</el-tag>
      </div>
      <dl class="facts">
        <dt>类型 / 往来单位</dt>
        <dd>
          {{ returnKinds[data.document.kind] }} / {{ data.document.party_name }}
        </dd>
        <dt>原单 / 过账时间</dt>
        <dd>
          {{ data.document.source_number }} ·
          {{ localTime(data.document.source_posted_at) }}
        </dd>
        <dt>退货含税 / 未税金额</dt>
        <dd>
          {{ data.document.gross_amount }} / {{ data.document.net_amount }} 元
        </dd>
        <dt>申请原因</dt>
        <dd>{{ data.document.reason }}</dd>
        <template v-if="data.document.review_note"
          ><dt>复核记录</dt>
          <dd>{{ data.document.review_note }}</dd></template
        >
        <template v-if="data.result"
          ><dt>回转成本</dt>
          <dd>{{ quantity(data.result.cost_amount) }} 元</dd>
          <template v-if="data.document.kind === 'PURCHASE'"
            ><dt>采购退货价差</dt>
            <dd>
              {{ quantity(data.result.purchase_cost_variance) }} 元（原未税金额
              − 出库成本）
            </dd></template
          >
        </template>
      </dl>
      <el-table :data="data.lines"
        ><el-table-column
          prop="name"
          label="商品"
          min-width="190"
        /><el-table-column prop="lot_no" label="原批号" min-width="170" />
        <el-table-column label="本次退回数量" width="160"
          ><template #default="{ row }"
            >{{ quantity(row.base_qty) }} {{ row.base_unit }}</template
          ></el-table-column
        >
        <el-table-column label="本次含税金额（元）" width="160"
          ><template #default="{ row }">
            {{ row.gross_amount ?? "历史整批金额" }}
          </template></el-table-column
        >
      </el-table>
      <p class="form-note">
        退货不重新打开原订单。销售退回品验收通过并过账后才恢复可售库存；不合格品可拒收并登记交还，销毁、转不合格自有库存及特殊品种处置另行建设。
      </p>
      <section v-if="data.custody.length" aria-label="隔离保管记录">
        <h3>隔离保管记录</h3>
        <dl
          class="facts"
          v-for="record in data.custody"
          :key="record.created_at"
        >
          <dt>保管位置 / 收货时间</dt>
          <dd>{{ record.zone_label }} · {{ localTime(record.created_at) }}</dd>
          <dt>收货说明</dt>
          <dd>{{ record.note }}</dd>
          <dt>保管状态</dt>
          <dd>
            {{
              record.outcome === "ACCEPTED"
                ? "已验收转入自有库存"
                : record.outcome === "HANDED_BACK"
                  ? "已交还客户"
                  : "隔离保管中，不计可售库存与自有成本"
            }}
          </dd>
          <template v-if="record.close_note"
            ><dt>结清说明</dt>
            <dd>{{ record.close_note }}</dd></template
          >
        </dl>
      </section>
      <div class="form-actions">
        <el-button
          v-if="
            canHandleReturnCustody() &&
            needsCustody &&
            data.document.state === 'DRAFT'
          "
          type="primary"
          :disabled="busy"
          @click="begin('receive')"
          >登记隔离收货</el-button
        >
        <el-button
          v-if="
            can('returns.review') &&
            data.document.state === (needsCustody ? 'RECEIVED' : 'DRAFT')
          "
          type="primary"
          :disabled="busy"
          @click="begin('review')"
          >独立复核退货</el-button
        >
        <el-button
          v-if="can('returns.post') && data.document.state === 'APPROVED'"
          type="primary"
          :disabled="busy"
          @click="begin('post')"
          >{{
            data.document.kind !== "PURCHASE" ? "确认退货入库" : "确认退货出库"
          }}</el-button
        >
        <el-button
          v-if="
            canApplyReturn(data.document.kind) &&
            ['DRAFT', 'APPROVED'].includes(data.document.state) &&
            !holding
          "
          :disabled="busy"
          @click="begin('cancel')"
          >取消退货申请</el-button
        >
        <el-button
          v-if="canHandleReturnCustody() && holding"
          :disabled="busy"
          @click="begin('hand-back')"
          >登记交还客户</el-button
        >
      </div>
      <template v-if="data.document.kind !== 'RETAIL'">
        <h3>原单有效核销</h3>
        <p class="form-note">
          原单未结余额不足以抵减本次退货时，需要撤销相关核销。撤销核销只恢复款项可用余额，不转移资金；实际退款在退货过账后另行登记。
        </p>
        <el-table :data="data.allocations" empty-text="没有有效核销">
          <el-table-column
            prop="cash_number"
            label="原收付款单"
            min-width="250"
          /><el-table-column prop="amount" label="核销金额（元）" width="150" />
          <el-table-column
            v-if="can('finance.reverse')"
            label="操作"
            width="120"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                :disabled="busy"
                @click="begin('reverse', row.id)"
                >撤销原核销</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <div class="pager">
          <el-button
            :disabled="!offset || loading"
            @click="
              offset -= 50;
              load();
            "
            >上一页</el-button
          ><el-button
            :disabled="!data.allocations_has_more || loading"
            @click="
              offset += 50;
              load();
            "
            >下一页</el-button
          >
        </div>
      </template>
    </section>
    <RetailReturnRefund
      v-if="
        data?.document.state === 'POSTED' && data.document.kind === 'RETAIL'
      "
      :id="id"
      @changed="load"
      @failure="emit('failure', $event)"
    />
    <ReturnRefunds
      v-if="
        data?.document.state === 'POSTED' &&
        data.document.kind !== 'RETAIL' &&
        can('finance.read')
      "
      :id="id"
      :kind="data.document.kind"
      @failure="emit('failure', $event)"
    />
  </div>
  <el-dialog
    v-model="open"
    :title="
      mode === 'review'
        ? '独立复核退货'
        : mode === 'receive'
          ? '登记隔离收货'
          : mode === 'hand-back'
            ? '登记交还客户'
            : mode === 'post'
              ? '确认退货过账'
              : mode === 'reverse'
                ? '撤销原核销'
                : '取消退货申请'
    "
    width="840px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="submit">
      <template v-if="['review', 'receive'].includes(mode)">
        <p class="form-note">
          请现场核对本次申请各行的实物数量与批号。销售退回品由制单人、收货人以外的人员验收质量；复核通过后仍需确认过账。
        </p>
        <label v-if="mode === 'review'"
          >复核结论<select v-model="decision">
            <option value="APPROVE">复核通过</option>
            <option value="REJECT">
              {{ needsCustody ? "拒收，等待交还实物" : "拒绝并取消申请" }}
            </option>
          </select></label
        >
        <div
          v-for="(line, i) in checks"
          :key="line.source_posting_line_id"
          class="form-grid"
        >
          <p>{{ line.name }}</p>
          <label
            >实物核对数量<input
              v-model="line.qty"
              :aria-label="
                (mode === 'receive' ? '收货数量 ' : '实物核对数量 ') + (i + 1)
              "
              :required="decision === 'APPROVE'"
              inputmode="decimal"
              pattern="[0-9]+([.][0-9]{1,6})?"
          /></label>
          <label
            >实物批号<input
              v-model="line.lot_no"
              :aria-label="'实物批号 ' + (i + 1)"
              :required="decision === 'APPROVE'"
              maxlength="100"
          /></label>
        </div>
      </template>
      <label v-if="mode === 'receive'"
        >隔离保管位置<input
          v-model="zone"
          required
          maxlength="200"
          placeholder="例如：一号仓退货隔离区 A-01；请填写实际位置"
      /></label>
      <p v-if="mode === 'hand-back'" class="form-note">
        仅在本次申请全部实物已交还客户后保存。保存后关闭保管记录、取消本次退货，不改变库存、成本或往来账。
      </p>
      <p v-if="mode === 'post'" class="form-note">
        请确认实物交接已完成。过账将按本次申请数量回转库存与成本；批发 /
        采购同时冲减往来账，零售退款需在入库后另行登记。完成后不能直接取消或编辑。
      </p>
      <label v-if="['receive', 'hand-back'].includes(mode)"
        ><input
          type="checkbox"
          v-model="confirmed"
          required
        />我已核对本次全部实物交接完成</label
      >
      <label
        >操作说明<textarea v-model="reason" required maxlength="200" />
      </label>
      <div class="form-actions">
        <el-button native-type="submit" type="primary" :loading="busy"
          >确认保存</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
