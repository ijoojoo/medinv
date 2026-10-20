<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request, session } from "./api";
import { can, positive, quantity, useCommand, type PageData } from "./business";
import { localTime } from "./sales";
import {
  lossStates,
  type TransferDocument,
  type TransferLine,
  type TransferLoss,
} from "./transfers";
const props = defineProps<{
  transfer: TransferDocument;
  initialId?: string;
  lines: TransferLine[];
  disabled: boolean;
}>();
const emit = defineEmits<{
  failure: [error: unknown];
  changed: [];
  busy: [value: boolean];
}>();
const data = ref<PageData<TransferLoss>>({ items: [], has_more: false }),
  offset = ref(0),
  loading = ref(false);
const detail = ref<TransferLoss | null>(null),
  mode = ref(""),
  action = ref("");
const lineId = ref(""),
  qty = ref(""),
  lot = ref(""),
  reason = ref(""),
  evidence = ref(""),
  decision = ref("APPROVE");
const available = computed(() =>
  props.lines.filter((r) => positive(r.remaining_qty)),
);
const line = computed(() => available.value.find((r) => r.id === lineId.value));
const independent = computed(
  () => detail.value?.created_by !== session.value?.principal_id,
);
const open = computed(() =>
  ["IN_TRANSIT", "PART_RECEIVED"].includes(props.transfer.state),
);
const path = computed(() => `/api/v1/transfers/${props.transfer.id}/losses`);
const { busy, execute } = useCommand((e) => emit("failure", e));
watch(busy, (value) => emit("busy", value));
let epoch = 0,
  operation = "";
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<PageData<TransferLoss>>(
      `${path.value}?offset=${offset.value}`,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function inspect(id: string) {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<TransferLoss>(`${path.value}/${id}`);
    if (current === epoch) {
      detail.value = result;
      mode.value = "detail";
      action.value = "";
    }
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin() {
  operation = crypto.randomUUID();
  mode.value = "create";
  action.value = "";
  lineId.value = available.value[0]?.id || "";
  qty.value = "";
  lot.value = "";
  reason.value = "";
  evidence.value = "";
}
function beginAction(value: string) {
  action.value = value;
  reason.value = "";
  decision.value = "APPROVE";
}
async function submit() {
  if (mode.value === "create") {
    await execute<{ id: string }>(
      path.value,
      {
        client_operation_id: operation,
        expected_version: props.transfer.version,
        line_id: lineId.value,
        qty: qty.value,
        lot_no: lot.value,
        reason: reason.value,
        evidence: evidence.value,
      },
      async (result) => {
        offset.value = 0;
        await load();
        await inspect(result.id);
        emit("changed");
      },
    );
  } else if (detail.value && action.value) {
    const id = detail.value.id;
    await execute(
      `${path.value}/${id}/${action.value}`,
      {
        expected_version: detail.value.version,
        reason: reason.value,
        ...(action.value === "review" ? { decision: decision.value } : {}),
      },
      async () => {
        await load();
        await inspect(id);
        emit("changed");
      },
    );
  }
}
onMounted(async () => {
  await load();
  if (props.initialId) await inspect(props.initialId);
});
watch(
  () => props.transfer.version,
  () => load(),
);
</script>
<template>
  <section v-loading="loading" data-testid="transfer-losses">
    <div class="business-toolbar">
      <h3>在途损失处理</h3>
      <el-button :disabled="busy || disabled || loading" @click="load"
        >刷新损失记录</el-button
      >
      <el-button
        v-if="open && can('transfers.loss.write')"
        :disabled="busy || disabled || loading || !available.length"
        @click="begin"
        >申请在途损失</el-button
      >
    </div>
    <p class="form-note">
      仅登记已核实的运输短少。申请和审核不占用在途库存；实际到货仍按收货处理，核销前重新核对余量。每行同时只能有一张待处理申请。
    </p>
    <el-table :data="data.items" empty-text="尚无在途损失申请">
      <el-table-column label="损失单号" min-width="260"
        ><template #default="s"
          ><el-button
            link
            type="primary"
            :disabled="busy || disabled"
            @click="inspect(s.row.id)"
            >{{ s.row.number }}</el-button
          ></template
        ></el-table-column
      >
      <el-table-column label="原调拨行 / 商品" min-width="180"
        ><template #default="s"
          >{{ s.row.line_no }}. {{ s.row.name }}</template
        ></el-table-column
      >
      <el-table-column label="数量" width="100"
        ><template #default="s"
          >{{ quantity(s.row.qty) }} {{ s.row.base_unit }}</template
        ></el-table-column
      >
      <el-table-column label="状态" width="100"
        ><template #default="s">{{
          lossStates[s.row.state]
        }}</template></el-table-column
      >
      <el-table-column label="已核销成本（元）" min-width="150"
        ><template #default="s">{{
          s.row.result?.loss_cost ?? "--"
        }}</template></el-table-column
      >
    </el-table>
    <div class="pager">
      <el-button
        :disabled="busy || disabled || loading || !offset"
        @click="
          offset -= 50;
          load();
        "
        >上一页损失</el-button
      ><el-button
        :disabled="busy || disabled || loading || !data.has_more"
        @click="
          offset += 50;
          load();
        "
        >下一页损失</el-button
      >
    </div>
  </section>
  <el-dialog
    :model-value="!!mode"
    :title="mode === 'create' ? '申请在途损失' : '在途损失详情'"
    width="680px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    @close="
      mode = '';
      action = '';
    "
  >
    <form
      v-if="mode === 'create'"
      class="business-form"
      @submit.prevent="submit"
    >
      <label
        >调拨明细<select
          v-model="lineId"
          aria-label="调拨明细"
          :disabled="busy"
          required
          @change="
            qty = '';
            lot = '';
          "
        >
          <option v-for="r in available" :key="r.id" :value="r.id">
            {{ r.name }} · {{ r.lot_no }} · 调出库位 {{ r.source_location }}
          </option>
        </select></label
      >
      <p v-if="line" class="form-note">
        当前剩余在途 {{ quantity(line.remaining_qty) }}
        {{ line.base_unit }}。请填写已核实的短少数量，不会默认核销全部余量。
      </p>
      <div class="form-grid">
        <label
          >损失数量<input
            v-model="qty"
            required
            inputmode="decimal"
            maxlength="22"
            :disabled="busy"
        /></label>
        <label
          >确认批号<input
            v-model="lot"
            required
            maxlength="200"
            :disabled="busy"
        /></label>
      </div>
      <label
        >损失原因<input
          v-model="reason"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <label
        >核查依据<input
          v-model="evidence"
          required
          maxlength="200"
          placeholder="运输差异、承运核查等记录编号及结论"
          :disabled="busy"
      /></label>
      <div class="business-toolbar">
        <el-button :disabled="busy" @click="mode = ''">返回</el-button
        ><el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="!line"
          >提交损失申请</el-button
        >
      </div>
    </form>
    <template v-else-if="detail">
      <dl class="facts">
        <dt>损失单号</dt>
        <dd class="mono">{{ detail.number }}</dd>
        <dt>状态</dt>
        <dd data-testid="loss-state">{{ lossStates[detail.state] }}</dd>
        <dt>调拨行 / 商品</dt>
        <dd>{{ detail.line_no }}. {{ detail.name }}</dd>
        <dt>批号</dt>
        <dd>{{ detail.lot_no }}</dd>
        <dt>损失数量</dt>
        <dd>{{ quantity(detail.qty) }} {{ detail.base_unit }}</dd>
        <dt>损失原因</dt>
        <dd>{{ detail.reason }}</dd>
        <dt>核查依据</dt>
        <dd>{{ detail.evidence }}</dd>
        <dt>申请时间</dt>
        <dd>{{ localTime(detail.created_at) }}</dd>
        <template v-if="detail.review_note"
          ><dt>审核意见</dt>
          <dd>{{ detail.review_note }}</dd></template
        >
        <template v-if="detail.cancel_note"
          ><dt>取消原因</dt>
          <dd>{{ detail.cancel_note }}</dd></template
        >
        <template v-if="detail.result"
          ><dt>已核销成本</dt>
          <dd>{{ detail.result.loss_cost }} 元</dd>
          <dt>过账凭证</dt>
          <dd class="mono">{{ detail.result.posting_id }}</dd></template
        >
      </dl>
      <p
        class="form-note"
        v-if="!open && ['DRAFT', 'APPROVED'].includes(detail.state)"
      >
        调拨已结清，本申请不能继续核销。请核对实际到货记录后取消或拒绝申请。
      </p>
      <div v-if="!action" class="business-toolbar">
        <el-button
          v-if="
            detail.state === 'DRAFT' &&
            independent &&
            can('transfers.loss.review')
          "
          :disabled="busy || loading"
          type="primary"
          @click="beginAction('review')"
          >独立审核损失</el-button
        >
        <el-button
          v-if="
            detail.state === 'APPROVED' &&
            open &&
            independent &&
            can('transfers.loss.post')
          "
          :disabled="busy || loading"
          type="primary"
          @click="beginAction('post')"
          >确认损失核销</el-button
        >
        <el-button
          v-if="
            ['DRAFT', 'APPROVED'].includes(detail.state) &&
            can('transfers.loss.write')
          "
          :disabled="busy || loading"
          @click="beginAction('cancel')"
          >取消损失申请</el-button
        >
        <el-button :disabled="busy || loading" @click="inspect(detail.id)"
          >刷新损失详情</el-button
        >
        <el-button :disabled="busy" @click="mode = ''">关闭详情</el-button>
      </div>
      <form v-else class="business-form" @submit.prevent="submit">
        <label v-if="action === 'review'"
          >审核结论<select
            v-model="decision"
            aria-label="审核结论"
            :disabled="busy"
          >
            <option value="APPROVE">通过</option>
            <option value="REJECT">拒绝并取消</option>
          </select></label
        >
        <p v-if="action === 'post'" class="form-note">
          确认将从本单在途扣减 {{ quantity(detail.qty) }}
          {{
            detail.base_unit
          }}，同步核销当前批次成本。此操作不登记实收、不产生收付款，已核销记录不能直接撤销。
        </p>
        <label
          >操作说明<input
            v-model="reason"
            required
            maxlength="200"
            :disabled="busy"
        /></label>
        <div class="business-toolbar">
          <el-button :disabled="busy" @click="action = ''">返回操作</el-button
          ><el-button native-type="submit" type="primary" :loading="busy"
            >保存损失处理</el-button
          >
        </div>
      </form>
    </template>
  </el-dialog>
</template>
