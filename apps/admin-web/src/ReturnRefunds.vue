<script setup lang="ts">
import { businessToday } from "./dates";
import { registerCatalogChanges } from "./catalogChanges";
import DateInput from "./DateInput.vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { can, positive, useCommand, type PageData } from "./business";
import { localTime } from "./sales";
interface Refund {
  id: string;
  number: string;
  cash_number: string;
  account_name: string;
  amount: string;
  reference: string;
  state: string;
  void_reason: string | null;
  created_at: string;
}
interface CashChoice {
  id: string;
  number: string;
  entity_id: string;
  party_id: string;
  account_id: string;
  account_name: string;
  direction: string;
  remaining_amount: string;
  version: string;
}
const props = defineProps<{ id: string; kind: string }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const rows = ref<PageData<Refund> & { remaining_amount: string }>({
  items: [],
  has_more: false,
  remaining_amount: "0",
});
const offset = ref(0),
  loading = ref(false),
  mode = ref(""),
  target = ref(""),
  reason = ref(""),
  cashId = ref(""),
  choiceOffset = ref(0),
  choiceLoading = ref(false);
const choices = ref<PageData<CashChoice>>({ items: [], has_more: false });
const today = businessToday;
const form = ref({
  amount: "",
  reference: "",
  occurred_on: today(),
  note: "",
  confirmed: false,
});
const currentCash = computed(() =>
  choices.value.items.find((c) => c.id === cashId.value),
);
const open = computed({
  get: () => !!mode.value,
  set: (v: boolean) => {
    if (!v) {
      mode.value = "";
      choiceEpoch++;
    }
  },
});
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: !!mode.value, busy: busy.value }),
    "returns",
  ),
);
let epoch = 0,
  choiceEpoch = 0,
  operation = "";
onUnmounted(() => {
  epoch++;
  choiceEpoch++;
});
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<typeof rows.value>(
      "/api/v1/returns/" + props.id + "/refunds?offset=" + offset.value,
    );
    if (current === epoch) rows.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function loadChoices() {
  const current = ++choiceEpoch;
  cashId.value = "";
  choiceLoading.value = true;
  try {
    const result = await request<PageData<CashChoice>>(
      "/api/v1/returns/" +
        props.id +
        "/refund-options?offset=" +
        choiceOffset.value,
    );
    if (current === choiceEpoch) choices.value = result;
  } catch (e) {
    if (current === choiceEpoch) emit("failure", e);
  } finally {
    if (current === choiceEpoch) choiceLoading.value = false;
  }
}
function begin(value: string, id = "") {
  mode.value = value;
  target.value = id;
  reason.value = "";
  operation = crypto.randomUUID();
  if (value === "refund") {
    form.value = {
      amount: "",
      reference: "",
      occurred_on: today(),
      note: "",
      confirmed: false,
    };
    choices.value = { items: [], has_more: false };
    choiceOffset.value = 0;
    void loadChoices();
  }
}
async function submit() {
  if (mode.value === "void") {
    await execute(
      "/api/v1/return-refunds/" + target.value + "/void",
      { client_operation_id: operation, reason: reason.value },
      async () => {
        open.value = false;
        await load();
      },
    );
  } else {
    const c = currentCash.value;
    if (!c) return;
    await execute(
      "/api/v1/return-refunds",
      {
        ...form.value,
        client_operation_id: operation,
        cash_document_id: c.id,
        return_document_id: props.id,
        expected_cash_version: c.version,
        entity_id: c.entity_id,
        party_id: c.party_id,
        account_id: c.account_id,
        direction: c.direction === "RECEIPT" ? "PAYMENT" : "RECEIPT",
      },
      async () => {
        open.value = false;
        await load();
      },
    );
  }
}
onMounted(load);
</script>
<template>
  <section class="panel" v-loading="loading">
    <div class="panel-heading">
      <h3>{{ kind === "SALES" ? "退还客户款项" : "收到供应商退款" }}</h3>
      <el-button
        v-if="can('finance.reverse')"
        type="primary"
        :disabled="busy || !positive(rows.remaining_amount)"
        @click="begin('refund')"
        >登记实际退款</el-button
      >
    </div>
    <p class="form-note">
      退货尚未登记退款金额
      {{ rows.remaining_amount }}
      元。只可使用曾核销原单且仍有余额的原收付款记录，退款使用原账户。未付款的退货无需登记退款；本页面不执行银行转账。
    </p>
    <el-table :data="rows.items" empty-text="暂无退款登记">
      <el-table-column label="退款单 / 原款项" min-width="260"
        ><template #default="{ row }"
          ><span class="mono">{{ row.number }}</span
          ><small class="cell-sub">{{ row.cash_number }}</small></template
        ></el-table-column
      >
      <el-table-column prop="amount" label="金额（元）" width="120" />
      <el-table-column label="账户 / 凭据" min-width="170"
        ><template #default="{ row }"
          >{{ row.account_name
          }}<small class="cell-sub">{{ row.reference }}</small></template
        ></el-table-column
      >
      <el-table-column label="登记时间" min-width="165"
        ><template #default="{ row }">{{
          localTime(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column label="状态" min-width="130"
        ><template #default="{ row }"
          >{{ row.state === "VOIDED" ? "已作废" : "已登记"
          }}<small v-if="row.void_reason" class="cell-sub">{{
            row.void_reason
          }}</small></template
        ></el-table-column
      >
      <el-table-column v-if="can('finance.reverse')" label="操作" width="120"
        ><template #default="{ row }"
          ><el-button
            v-if="row.state === 'CONFIRMED'"
            link
            type="primary"
            :disabled="busy"
            @click="begin('void', row.id)"
            >作废退款登记</el-button
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
        :disabled="!rows.has_more || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
  <el-dialog
    v-model="open"
    :title="mode === 'refund' ? '登记实际退款' : '作废退款登记'"
    width="740px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="submit">
      <fieldset class="command-fields" :disabled="busy">
        <template v-if="mode === 'refund'">
          <p class="form-note">
            {{
              kind === "SALES"
                ? "确认已经向客户退款。"
                : "确认已经收到供应商退款。"
            }}未发生资金收付时请勿登记。
          </p>
          <div v-loading="choiceLoading">
            <label
              >原收付款单<select v-model="cashId" required>
                <option value="" disabled>请选择可退款的原款项</option>
                <option v-for="c in choices.items" :key="c.id" :value="c.id">
                  {{ c.number }} · {{ c.account_name }} · 可退
                  {{ c.remaining_amount }} 元
                </option>
              </select></label
            >
            <p v-if="!choices.items.length && !choiceLoading" class="form-note">
              没有符合条件的原款项。原款项须曾核销本原单，且仍有未核销、未退余额。
            </p>
            <div class="pager">
              <el-button
                :disabled="!choiceOffset || choiceLoading || busy"
                @click="
                  choiceOffset -= 50;
                  loadChoices();
                "
                >上一页款项</el-button
              ><el-button
                :disabled="!choices.has_more || choiceLoading || busy"
                @click="
                  choiceOffset += 50;
                  loadChoices();
                "
                >下一页款项</el-button
              >
            </div>
          </div>
          <div class="form-grid">
            <label
              >实际退款金额（元）<input
                v-model="form.amount"
                required
                inputmode="decimal"
                pattern="[0-9]+([.][0-9]{1,2})?"
                maxlength="21" /></label
            ><label
              >实际退款日期<DateInput
                v-model="form.occurred_on"
                required
                :max="today()"
                :disabled="busy"
            /></label>
          </div>
          <label
            >退款凭据编号<input
              v-model="form.reference"
              required
              maxlength="120" /></label
          ><label
            >退款核对说明<textarea
              v-model="form.note"
              required
              maxlength="500"
            />
          </label>
          <label class="confirmation"
            ><input
              v-model="form.confirmed"
              required
              type="checkbox"
            />我已核对退款实际发生及退款凭据</label
          >
        </template>
        <template v-else
          ><p class="form-note">
            仅用于修正错误登记，恢复原款项可用余额。该操作不会撤回现实中的转账，也不撤销已完成的退货。
          </p>
          <label
            >作废原因<textarea
              v-model="reason"
              required
              maxlength="200"
            /></label
        ></template>
      </fieldset>
      <div class="form-actions">
        <el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="mode === 'refund' && (!currentCash || choiceLoading)"
          >确认保存退款记录</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
<style scoped>
.command-fields {
  display: grid;
  gap: 16px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
</style>
