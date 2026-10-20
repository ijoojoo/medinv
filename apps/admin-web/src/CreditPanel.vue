<script setup lang="ts">
import PartyLookup from "./PartyLookup.vue";
import DateInput from "./DateInput.vue";
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import type { Credit } from "./sales";
const props = defineProps<{
  entity: string;
}>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const customerName = ref("");
const customer = ref(""),
  data = ref<Credit | null>(null),
  loading = ref(false),
  editing = ref(false);
const form = ref({ credit_limit: "", valid_until: "", reason: "" });
const { busy, execute } = useCommand((error) => emit("failure", error));
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: editing.value, busy: busy.value }),
    "selling",
  ),
);
let epoch = 0;
onUnmounted(() => epoch++);
watch(
  () => props.entity,
  () => {
    epoch++;
    customer.value = "";
    data.value = null;
    editing.value = false;
  },
);
watch(customer, () => void load());
async function load() {
  const current = ++epoch;
  data.value = null;
  if (!customer.value) {
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    const result = await request<Credit>(
      `/api/v1/customer-credit/${customer.value}?entity_id=${props.entity}`,
    );
    if (current === epoch) data.value = result;
  } catch (error) {
    if (current === epoch) emit("failure", error);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function edit() {
  if (!data.value) return;
  form.value = {
    credit_limit: data.value.credit_limit,
    valid_until: data.value.valid_until || "",
    reason: "",
  };
  editing.value = true;
}
async function save() {
  if (!data.value) return;
  await execute(
    `/api/v1/customer-credit/${customer.value}?entity_id=${props.entity}`,
    {
      expected_version: data.value.version,
      ...form.value,
    },
    async () => {
      editing.value = false;
      await load();
    },
  );
}
</script>
<template>
  <section class="credit-panel" v-loading="loading">
    <div class="business-toolbar">
      <label
        >客户信用账户<PartyLookup
          v-model="customer"
          :entity="entity"
          channel="sale"
          label="客户信用账户"
          :disabled="busy || editing"
          @selected="customerName = $event?.name || ''"
      /></label>
      <el-button :disabled="!customer || busy || editing" @click="load"
        >刷新额度</el-button
      >
      <el-button
        v-if="can('sales.credit')"
        :disabled="!data || busy || editing"
        @click="edit"
        >设置客户额度</el-button
      >
    </div>
    <div v-if="data" class="credit-grid">
      <div>
        <small>授信额度（元）</small><b>{{ data.credit_limit }}</b>
      </div>
      <div>
        <small>订单占用（元）</small><b>{{ data.exposure }}</b>
      </div>
      <div>
        <small>应收余额（元）</small><b>{{ data.receivable }}</b>
      </div>
      <div>
        <small>可用额度（元）</small><b>{{ data.available }}</b>
      </div>
      <div>
        <small>额度有效期</small><b>{{ data.valid_until || "尚未设置" }}</b>
      </div>
    </div>
    <p class="form-note">
      审核订单时占用额度，出库后转为应收。客户有逾期应收或授信过期时暂停新增审批和发货；收款核销在“往来结算”办理，撤销核销会恢复应收占用。
    </p>
  </section>
  <el-dialog
    v-model="editing"
    title="设置客户额度"
    width="560px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="save">
      <fieldset class="command-fields" :disabled="busy">
        <p class="form-note">
          {{ customerName }} · 额度不得低于已占用额度与应收余额之和。
        </p>
        <label
          >授信额度（元）<input
            v-model="form.credit_limit"
            required
            inputmode="decimal"
            pattern="[0-9]+([.][0-9]{1,2})?"
            maxlength="21"
        /></label>
        <label
          >额度有效期<DateInput
            v-model="form.valid_until"
            :disabled="busy"
            required
        /></label>
        <label
          >调整依据<textarea
            v-model="form.reason"
            required
            maxlength="500"
          ></textarea>
        </label>
      </fieldset>
      <div class="form-actions">
        <el-button :disabled="busy" @click="editing = false">取消</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >保存客户额度</el-button
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
