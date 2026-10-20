<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { can, positive, useCommand } from "./business";
import { localTime } from "./sales";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ failure: [error: unknown]; changed: [] }>();
interface RefundData {
  receipt: {
    account_name: string;
    amount: string;
    tendered: string;
    change_amount: string;
  };
  refund: {
    number: string;
    amount: string;
    note: string;
    created_at: string;
  } | null;
  remaining_amount: string;
  version: string;
  state: string;
}
const data = ref<RefundData | null>(null),
  loading = ref(false),
  open = ref(false);
const reason = ref(""),
  confirmed = ref(false),
  operation = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<RefundData>(
      `/api/v1/returns/${props.id}/retail-refund`,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin() {
  operation.value = crypto.randomUUID();
  reason.value = "";
  confirmed.value = false;
  open.value = true;
}
async function save() {
  if (!data.value) return;
  await execute(
    `/api/v1/returns/${props.id}/retail-refund`,
    {
      client_operation_id: operation.value,
      expected_version: data.value.version,
      reason: reason.value,
      confirmed: confirmed.value,
    },
    async () => {
      open.value = false;
      await load();
      emit("changed");
    },
  );
}
onMounted(load);
</script>
<template>
  <section class="panel" v-loading="loading" aria-label="零售退货现金退款">
    <div class="panel-heading">
      <h3>原现金账户退款</h3>
      <el-button :disabled="busy" @click="load">刷新零售退款记录</el-button>
    </div>
    <template v-if="data">
      <dl class="facts">
        <dt>原现金账户</dt>
        <dd>{{ data.receipt.account_name }}</dd>
        <dt>原单净收</dt>
        <dd>
          {{ data.receipt.amount }} 元（交付现金
          {{ data.receipt.tendered }} 元，找零
          {{ data.receipt.change_amount }} 元）
        </dd>
        <dt>本次待退现金</dt>
        <dd>
          <strong>{{ data.remaining_amount }} 元</strong>
        </dd>
      </dl>
      <template v-if="data.refund"
        ><el-alert
          title="本次退货已登记现金退款，请勿重复退还"
          type="success"
          :closable="false"
        />
        <dl class="facts">
          <dt>退款单号</dt>
          <dd class="mono">{{ data.refund.number }}</dd>
          <dt>已退金额</dt>
          <dd>{{ data.refund.amount }} 元</dd>
          <dt>退款登记时间</dt>
          <dd>{{ localTime(data.refund.created_at) }}</dd>
          <dt>操作说明</dt>
          <dd>{{ data.refund.note }}</dd>
        </dl></template
      >
      <p v-else-if="!positive(data.remaining_amount)" class="form-note">
        本次退货金额为零，无需退还现金。
      </p>
      <p v-else class="form-note">
        退货入库已经完成，现金尚未登记退还。实际将本次金额退给顾客后保存退款记录；网络异常请核对原记录或重试同一表单，勿重复退钱。
      </p>
      <div class="form-actions">
        <el-button
          v-if="can('retail.refund')"
          type="primary"
          :disabled="
            busy ||
            loading ||
            !!data.refund ||
            !positive(data.remaining_amount) ||
            data.state !== 'POSTED'
          "
          @click="begin"
          >登记本次零售退款</el-button
        >
      </div>
    </template>
  </section>
  <el-dialog
    v-model="open"
    title="登记零售退货现金退款"
    class="business-dialog"
    width="660px"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
  >
    <form v-if="data" class="business-form" @submit.prevent="save">
      <fieldset :disabled="busy">
        <p>
          请向顾客退还
          <strong>{{ data.remaining_amount }} 元</strong>，记入原账户
          {{ data.receipt.account_name }}。
        </p>
        <label
          >退款核对说明<textarea
            v-model="reason"
            required
            maxlength="200"
            rows="3"
          />
        </label>
        <label class="confirmation-check"
          ><input
            v-model="confirmed"
            type="checkbox"
            required
          />我已按本次退货金额实际退还现金</label
        >
        <div class="form-actions">
          <el-button @click="open = false">返回</el-button
          ><el-button type="primary" native-type="submit" :loading="busy"
            >确认登记零售退款</el-button
          >
        </div>
      </fieldset>
    </form>
  </el-dialog>
</template>
