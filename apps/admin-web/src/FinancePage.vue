<script setup lang="ts">
import { businessToday } from "./dates";
import DateInput from "./DateInput.vue";
import { registerCatalogChanges } from "./catalogChanges";
import { activeEntity } from "./entityScope";
import { onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, positive, useCommand, type PageData } from "./business";
import {
  directions,
  type Account,
  type CashDocument,
  type SettlementItem,
} from "./finance";
import CashDetail from "./CashDetail.vue";
const emit = defineEmits<{ failure: [error: unknown] }>();
const entity = ref(activeEntity.value),
  tab = ref("AR"),
  offset = ref(0),
  loading = ref(false),
  selected = ref("");
const items = ref<PageData<SettlementItem>>({ items: [], has_more: false }),
  cash = ref<PageData<CashDocument>>({ items: [], has_more: false });
const accounts = ref<Account[]>([]),
  accountOpen = ref(false),
  cashOpen = ref(false),
  source = ref<SettlementItem | null>(null);
const accountForm = ref({ code: "", name: "", method: "BANK" });
const today = businessToday;
const cashForm = ref({
  client_operation_id: "",
  account_id: "",
  amount: "",
  reference: "",
  occurred_on: today(),
  note: "",
  confirmed: false,
});
const { busy, execute } = useCommand((error) => emit("failure", error));
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (!entity.value) return;
  if (reset) {
    offset.value = 0;
    selected.value = "";
    items.value = { items: [], has_more: false };
    cash.value = { items: [], has_more: false };
  }
  const current = ++epoch;
  loading.value = true;
  try {
    const listPath =
      tab.value === "CASH"
        ? `/api/v1/settlements/cash-documents?entity_id=${entity.value}&offset=${offset.value}`
        : `/api/v1/settlements/items?entity_id=${entity.value}&kind=${tab.value === "AP" ? "AP" : "AR"}&offset=${offset.value}`;
    const [rows, choices] = await Promise.all([
      request<PageData<SettlementItem> | PageData<CashDocument>>(listPath),
      request<{ items: Account[] }>(
        `/api/v1/settlements/accounts?entity_id=${entity.value}`,
      ),
    ]);
    if (current === epoch) {
      accounts.value = choices.items;
      if (tab.value === "CASH") cash.value = rows as PageData<CashDocument>;
      else items.value = rows as PageData<SettlementItem>;
    }
  } catch (error) {
    if (current === epoch) emit("failure", error);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function switchTab(value: string) {
  tab.value = value;
  void load(true);
}
function record(row: SettlementItem) {
  source.value = row;
  cashForm.value = {
    client_operation_id: crypto.randomUUID(),
    account_id: accounts.value[0]?.id || "",
    amount: "",
    reference: "",
    occurred_on: today(),
    note: "",
    confirmed: false,
  };
  cashOpen.value = true;
}
async function saveCash() {
  if (!source.value) return;
  await execute<{ id: string }>(
    "/api/v1/settlements/cash-documents",
    {
      ...cashForm.value,
      entity_id: entity.value,
      party_id: source.value.party_id,
      direction: source.value.kind === "AR" ? "RECEIPT" : "PAYMENT",
    },
    async (result) => {
      cashOpen.value = false;
      selected.value = result.id;
      tab.value = "CASH";
      await load();
    },
  );
}
async function saveAccount() {
  await execute(
    "/api/v1/settlements/accounts",
    { entity_id: entity.value, ...accountForm.value },
    async () => {
      accountOpen.value = false;
      await load();
    },
  );
}
onMounted(() => load());
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: accountOpen.value || cashOpen.value, busy: busy.value }),
    "finance",
  ),
);
</script>
<template>
  <CashDetail
    v-if="selected"
    :key="selected"
    :id="selected"
    @failure="emit('failure', $event)"
    @back="
      selected = '';
      load();
    "
  />
  <section v-else class="panel" v-loading="loading">
    <div class="business-toolbar">
      <el-button @click="load()">刷新往来账</el-button>
    </div>
    <div class="finance-tabs" role="tablist" aria-label="往来结算分类">
      <button
        v-for="item in [
          { key: 'AR', label: '应收账款' },
          { key: 'AP', label: '应付账款' },
          { key: 'CASH', label: '收付款记录' },
          { key: 'ACCOUNTS', label: '资金账户' },
        ]"
        :key="item.key"
        role="tab"
        :aria-selected="tab === item.key"
        :class="{ selected: tab === item.key }"
        @click="switchTab(item.key)"
      >
        {{ item.label }}
      </button>
    </div>
    <template v-if="tab === 'AR' || tab === 'AP'">
      <p class="form-note">
        {{
          tab === "AR"
            ? "收款核销后减少客户应收，恢复相应可用额度。"
            : "付款仅核销供应商应付，不自动抵扣该单位的客户应收。"
        }}
        未核销的款项请到“收付款记录”继续处理。
      </p>
      <el-table
        :data="items.items"
        empty-text="暂无应收应付记录，请先完成销售出库或采购入库"
      >
        <el-table-column
          prop="party_name"
          label="往来单位"
          min-width="170"
        /><el-table-column label="来源单据" min-width="230"
          ><template #default="{ row }"
            ><span class="mono">{{ row.source_number || row.posting_id }}</span
            ><small class="cell-sub">{{
              row.kind === "AR" ? "发货子单" : "收货单"
            }}</small></template
          ></el-table-column
        >
        <el-table-column
          prop="gross_amount"
          label="原始金额（元）"
          min-width="130"
        /><el-table-column
          prop="settled_amount"
          label="已核销（元）"
          min-width="120"
        /><el-table-column
          prop="returned_amount"
          label="已退货（元）"
          min-width="120"
        /><el-table-column
          prop="outstanding_amount"
          label="未结（元）"
          min-width="120"
        />
        <el-table-column label="状态" min-width="95"
          ><template #default="{ row }"
            ><el-tag
              :type="positive(row.outstanding_amount) ? 'warning' : 'success'"
              >{{
                positive(row.outstanding_amount) ? "未结清" : "已结清"
              }}</el-tag
            ></template
          ></el-table-column
        >
        <el-table-column v-if="can('finance.write')" label="操作" width="110"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="record(row)">{{
              tab === "AR" ? "登记收款" : "登记付款"
            }}</el-button></template
          ></el-table-column
        >
      </el-table>
    </template>
    <template v-else-if="tab === 'CASH'">
      <p class="form-note">
        这里只登记已经发生的款项和人工核对凭据。未调用银行或支付平台；登记后仍需核销具体应收
        / 应付。
      </p>
      <el-table
        :data="cash.items"
        empty-text="暂无收付款记录，可从应收或应付账款登记"
      >
        <el-table-column
          prop="number"
          label="收付款单号"
          min-width="240"
        /><el-table-column
          prop="party_name"
          label="往来单位"
          min-width="150"
        /><el-table-column label="方向" width="80"
          ><template #default="{ row }">{{
            directions[row.direction]
          }}</template></el-table-column
        ><el-table-column
          prop="amount"
          label="金额（元）"
          min-width="120"
        /><el-table-column label="未核销（元）" min-width="120"
          ><template #default="{ row }">{{
            row.state === "VOIDED" ? "—" : row.remaining_amount
          }}</template></el-table-column
        ><el-table-column label="状态" width="90"
          ><template #default="{ row }">{{
            row.state === "VOIDED" ? "已作废" : "已登记"
          }}</template></el-table-column
        ><el-table-column label="操作" width="110"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="selected = row.id"
              >款项详情</el-button
            ></template
          ></el-table-column
        >
      </el-table>
    </template>
    <template v-else>
      <div class="business-toolbar">
        <p class="form-note">
          账户用于区分现金或银行账簿，不代表银行连接或实时余额。
        </p>
        <el-button
          v-if="can('finance.manage')"
          type="primary"
          @click="
            accountForm = { code: '', name: '', method: 'BANK' };
            accountOpen = true;
          "
          >新增资金账户</el-button
        >
      </div>
      <el-table :data="accounts" empty-text="请由管理员建立资金账户"
        ><el-table-column prop="code" label="账户编码" /><el-table-column
          prop="name"
          label="账户名称"
        /><el-table-column label="类型"
          ><template #default="{ row }">{{
            row.method === "BANK" ? "银行账簿" : "现金账簿"
          }}</template></el-table-column
        ></el-table
      >
    </template>
    <div v-if="tab !== 'ACCOUNTS'" class="pager">
      <span>每页最多 50 条</span
      ><el-button
        :disabled="!offset || loading"
        @click="
          offset -= 50;
          load();
        "
        >上一页</el-button
      ><el-button
        :disabled="
          !(tab === 'CASH' ? cash.has_more : items.has_more) || loading
        "
        @click="
          offset += 50;
          load();
        "
        >下一页</el-button
      >
    </div>
  </section>
  <el-dialog
    v-model="accountOpen"
    title="新增资金账户"
    width="560px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
    ><form class="business-form" @submit.prevent="saveAccount">
      <label
        >账户编码<input
          v-model="accountForm.code"
          required
          maxlength="60" /></label
      ><label
        >账户名称<input
          v-model="accountForm.name"
          required
          maxlength="200" /></label
      ><label
        >账户类型<select v-model="accountForm.method">
          <option value="BANK">银行账簿</option>
          <option value="CASH">现金账簿</option>
        </select></label
      >
      <div class="form-actions">
        <el-button :disabled="busy" @click="accountOpen = false">取消</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >保存资金账户</el-button
        >
      </div>
    </form></el-dialog
  >
  <el-dialog
    v-model="cashOpen"
    :title="source?.kind === 'AR' ? '登记收款' : '登记付款'"
    width="680px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form v-if="source" class="business-form" @submit.prevent="saveCash">
      <p>
        <b>{{ source.party_name }}</b> · 本单未结
        {{ source.outstanding_amount }} 元
      </p>
      <p class="form-note">
        按实际收付净额登记，可分次收付，也可将同单位多笔单据合并核销。此处保存收付款事实，下一步再选择核销单据。
      </p>
      <label
        >资金账户<select v-model="cashForm.account_id" required>
          <option disabled value="">请先在资金账户页创建账户</option>
          <option v-for="a in accounts" :key="a.id" :value="a.id">
            {{ a.name }} · {{ a.code }}
          </option>
        </select></label
      >
      <div class="form-grid">
        <label
          >实际金额（元）<input
            v-model="cashForm.amount"
            required
            inputmode="decimal"
            pattern="[0-9]+([.][0-9]{1,2})?"
            maxlength="21" /></label
        ><label
          >实际收付日期<DateInput
            v-model="cashForm.occurred_on"
            :max="today()"
            required
        /></label>
      </div>
      <label
        >流水 / 凭据编号<input
          v-model="cashForm.reference"
          required
          maxlength="120" /></label
      ><label
        >核对说明<textarea
          v-model="cashForm.note"
          required
          maxlength="500"
        ></textarea></label
      ><label class="confirmation-check"
        ><input
          v-model="cashForm.confirmed"
          type="checkbox"
          required
        />我已核对款项实际发生及收付凭据</label
      >
      <div class="form-actions">
        <el-button :disabled="busy" @click="cashOpen = false">取消</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >确认登记款项</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
