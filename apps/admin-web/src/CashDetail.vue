<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { can, positive, useCommand, type PageData } from "./business";
import { localTime } from "./sales";
import {
  directions,
  type CashDocument,
  type Allocation,
  type SettlementItem,
} from "./finance";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ failure: [error: unknown]; back: [] }>();
const data = ref<{
    cash: CashDocument;
    allocations: Allocation[];
    has_more: boolean;
    void: { reason: string } | null;
  } | null>(null),
  loading = ref(false),
  offset = ref(0);
const mode = ref(""),
  reason = ref(""),
  target = ref(""),
  choiceOffset = ref(0),
  choices = ref<PageData<SettlementItem>>({ items: [], has_more: false }),
  choiceLoading = ref(false);
const chosen = ref<
  Record<
    string,
    {
      settlement_item_id: string;
      expected_item_version: string;
      amount: string;
    }
  >
>({});
const { busy, execute } = useCommand((error) => emit("failure", error));
const open = computed({
  get: () => !!mode.value,
  set: (v: boolean) => {
    if (!v) {
      mode.value = "";
      choiceEpoch++;
    }
  },
});
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
    const result = await request<typeof data.value>(
      `/api/v1/settlements/cash-documents/${props.id}?offset=${offset.value}`,
    );
    if (current === epoch) data.value = result;
  } catch (error) {
    if (current === epoch) emit("failure", error);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function loadChoices() {
  if (!data.value) return;
  const current = ++choiceEpoch;
  choiceLoading.value = true;
  try {
    const c = data.value.cash;
    const result = await request<PageData<SettlementItem>>(
      `/api/v1/settlements/items?entity_id=${c.entity_id}&party_id=${c.party_id}&kind=${c.direction === "RECEIPT" ? "AR" : "AP"}&open_only=true&offset=${choiceOffset.value}`,
    );
    if (current === choiceEpoch) {
      choices.value = result;
      for (const row of result.items) {
        if (!chosen.value[row.id])
          chosen.value[row.id] = {
            settlement_item_id: row.id,
            expected_item_version: row.version,
            amount: "0",
          };
      }
    }
  } catch (error) {
    if (current === choiceEpoch) emit("failure", error);
  } finally {
    if (current === choiceEpoch) choiceLoading.value = false;
  }
}
function begin(value: string, id = "") {
  mode.value = value;
  target.value = id;
  reason.value = "";
  operation = crypto.randomUUID();
  if (value === "allocate") {
    chosen.value = {};
    choices.value = { items: [], has_more: false };
    choiceOffset.value = 0;
    void loadChoices();
  }
}
async function submit() {
  if (!data.value) return;
  const c = data.value.cash;
  const path =
    mode.value === "allocate"
      ? "/api/v1/settlements/allocations"
      : mode.value === "reverse"
        ? `/api/v1/settlements/allocations/${target.value}/reverse`
        : `/api/v1/settlements/cash-documents/${props.id}/void`;
  const body =
    mode.value === "allocate"
      ? {
          client_operation_id: operation,
          cash_document_id: props.id,
          expected_cash_version: c.version,
          allocations: Object.values(chosen.value).filter((row) =>
            positive(row.amount),
          ),
        }
      : mode.value === "reverse"
        ? { client_operation_id: operation, reason: reason.value }
        : { expected_version: c.version, reason: reason.value };
  await execute(path, body, async () => {
    open.value = false;
    await load();
  });
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!mode.value, busy: busy.value }),
    "finance",
  ),
);
</script>
<template>
  <div v-loading="loading">
    <div class="business-toolbar">
      <el-button :disabled="busy" @click="emit('back')">返回往来账</el-button
      ><el-button :disabled="busy" @click="load">刷新款项</el-button>
    </div>
    <section v-if="data" class="panel">
      <div class="panel-heading">
        <h3 class="document-number">{{ data.cash.number }}</h3>
        <el-tag :type="data.cash.state === 'VOIDED' ? 'info' : 'success'">{{
          data.cash.state === "VOIDED" ? "已作废" : "已登记"
        }}</el-tag>
      </div>
      <dl class="facts">
        <dt>往来单位</dt>
        <dd>{{ data.cash.party_name }}</dd>
        <dt>收付方向 / 账户</dt>
        <dd>
          {{ directions[data.cash.direction] }} / {{ data.cash.account_name }}
        </dd>
        <dt>实际金额</dt>
        <dd>{{ data.cash.amount }} 元</dd>
        <dt>已核销金额</dt>
        <dd>{{ data.cash.allocated_amount }} 元</dd>
        <dt>已退款金额</dt>
        <dd>{{ data.cash.refunded_amount }} 元</dd>
        <dt>可继续核销</dt>
        <dd>
          {{ data.cash.state === "VOIDED" ? "—" : data.cash.remaining_amount }}
          元
        </dd>
        <dt>实际收付日期</dt>
        <dd>{{ data.cash.occurred_on }}</dd>
        <dt>流水 / 凭据</dt>
        <dd>{{ data.cash.reference }}</dd>
        <dt>人工核对说明</dt>
        <dd>{{ data.cash.note }}</dd>
        <template v-if="data.void"
          ><dt>作废原因</dt>
          <dd>{{ data.void.reason }}</dd></template
        >
      </dl>
      <div class="form-actions">
        <el-button
          v-if="can('finance.allocate') && data.cash.state === 'CONFIRMED'"
          type="primary"
          :disabled="busy || !positive(data.cash.remaining_amount)"
          @click="begin('allocate')"
          >核销单据</el-button
        ><el-button
          v-if="can('finance.reverse') && data.cash.state === 'CONFIRMED'"
          :disabled="
            busy ||
            positive(data.cash.allocated_amount) ||
            positive(data.cash.refunded_amount)
          "
          @click="begin('void')"
          >作废错误登记</el-button
        >
      </div>
      <p class="form-note">
        登记款项不会自动冲减应收应付。撤销核销恢复单据未结余额；作废仅修正错误登记，不执行现实中的退款或转账。
      </p>
      <h3>核销记录</h3>
      <el-table
        :data="data.allocations"
        empty-text="尚未核销，点击“核销单据”选择对应应收 / 应付"
      >
        <el-table-column label="来源单据" min-width="260"
          ><template #default="{ row }"
            ><span class="mono">{{ row.source_number || row.item_id }}</span>
            <small class="cell-sub">{{
              data.cash.direction === "RECEIPT" ? "发货子单" : "收货单"
            }}</small></template
          ></el-table-column
        ><el-table-column
          prop="amount"
          label="核销金额（元）"
          min-width="140"
        /><el-table-column label="登记时间" min-width="170"
          ><template #default="{ row }">{{
            localTime(row.created_at)
          }}</template></el-table-column
        ><el-table-column label="状态" min-width="140"
          ><template #default="{ row }"
            >{{ row.reversal_id ? "已撤销" : "有效"
            }}<small v-if="row.reversal_id" class="cell-sub"
              >{{ row.reversal_reason }} ·
              {{ localTime(row.reversed_at) }}</small
            ></template
          ></el-table-column
        ><el-table-column v-if="can('finance.reverse')" label="操作" width="110"
          ><template #default="{ row }"
            ><el-button
              v-if="!row.reversal_id"
              link
              type="primary"
              :disabled="busy"
              @click="begin('reverse', row.id)"
              >撤销核销</el-button
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
          :disabled="!data.has_more || loading"
          @click="
            offset += 50;
            load();
          "
          >下一页</el-button
        >
      </div>
    </section>
  </div>
  <el-dialog
    v-model="open"
    :title="
      mode === 'allocate'
        ? '核销单据'
        : mode === 'reverse'
          ? '撤销核销'
          : '作废错误登记'
    "
    width="900px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="submit">
      <template v-if="mode === 'allocate'"
        ><p class="form-note">
          {{ data?.cash.party_name }} · 款项可核销
          {{ data?.cash.remaining_amount }}
          元。填写每笔单据的本次核销金额，不核销填 0；一次最多 50
          笔，全部成功后一起保存。
        </p>
        <div v-loading="choiceLoading">
          <el-table :data="choices.items" empty-text="暂无该往来单位的未结单据"
            ><el-table-column label="来源单据 / 记账时间" min-width="270"
              ><template #default="{ row }"
                ><span class="mono">{{
                  row.source_number || row.posting_id
                }}</span
                ><small class="cell-sub">{{
                  localTime(row.created_at)
                }}</small></template
              ></el-table-column
            ><el-table-column
              prop="outstanding_amount"
              label="未结（元）"
              width="130" /><el-table-column label="本次核销（元）" width="180"
              ><template #default="{ row }"
                ><input
                  v-if="chosen[row.id]"
                  v-model="chosen[row.id]!.amount"
                  aria-label="本次核销金额"
                  required
                  inputmode="decimal"
                  pattern="[0-9]+([.][0-9]{1,2})?"
                  maxlength="21" /></template></el-table-column
          ></el-table>
        </div>
        <div class="pager">
          <span
            >已填写
            {{
              Object.values(chosen).filter((row) => positive(row.amount)).length
            }}
            笔</span
          ><el-button
            :disabled="!choiceOffset || busy || choiceLoading"
            @click="
              choiceOffset -= 50;
              loadChoices();
            "
            >上一页</el-button
          ><el-button
            :disabled="!choices.has_more || busy || choiceLoading"
            @click="
              choiceOffset += 50;
              loadChoices();
            "
            >下一页</el-button
          >
        </div>
      </template>
      <template v-else
        ><el-alert
          :title="
            mode === 'reverse'
              ? '撤销后将恢复单据未结金额；收款核销撤销也会恢复客户应收占用'
              : '仅用于修正错误登记；不会执行银行转账或退款'
          "
          type="warning"
          :closable="false" /><label
          >操作原因<textarea
            v-model="reason"
            required
            maxlength="200"
          ></textarea></label
      ></template>
      <div class="form-actions">
        <el-button :disabled="busy" @click="open = false">返回</el-button
        ><el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="choiceLoading"
          >{{
            mode === "allocate"
              ? "确认核销"
              : mode === "reverse"
                ? "确认撤销核销"
                : "确认作废登记"
          }}</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
