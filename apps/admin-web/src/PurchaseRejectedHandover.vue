<script setup lang="ts">
import { ref } from "vue";
import { can, positive, quantity, useCommand } from "./business";
import { localTime } from "./sales";

const props = defineProps<{
  receipt: {
    id: string;
    version: string;
    lines: {
      id: string;
      name: string;
      lot_no: string;
      base_unit: string;
      rejected_pending_qty: string;
    }[];
    rejected_handovers: {
      id: string;
      receipt_line_id: string;
      name: string;
      lot_no: string;
      qty: string;
      base_unit: string;
      handover_ref: string;
      recipient: string;
      reason: string;
      created_at: string;
    }[];
  };
}>();
const emit = defineEmits<{ saved: []; failure: [error: unknown] }>();
const opened = ref(false);
const form = ref({
  expected_version: "",
  handover_ref: "",
  recipient: "",
  reason: "",
  lines: [] as {
    receipt_line_id: string;
    name: string;
    lot_no: string;
    base_unit: string;
    pending: string;
    qty: string;
  }[],
});
const { busy, execute } = useCommand((error) => emit("failure", error));
function open() {
  form.value = {
    expected_version: props.receipt.version,
    handover_ref: "",
    recipient: "",
    reason: "",
    lines: props.receipt.lines
      .filter((line) => positive(line.rejected_pending_qty))
      .map((line) => ({
        receipt_line_id: line.id,
        name: line.name,
        lot_no: line.lot_no,
        base_unit: line.base_unit,
        pending: line.rejected_pending_qty,
        qty: "0",
      })),
  };
  opened.value = true;
}
async function save() {
  await execute(
    `/api/v1/receipts/${props.receipt.id}/rejected-handovers`,
    {
      expected_version: form.value.expected_version,
      handover_ref: form.value.handover_ref,
      recipient: form.value.recipient,
      reason: form.value.reason,
      lines: form.value.lines
        .filter((line) => positive(line.qty))
        .map((line) => ({
          receipt_line_id: line.receipt_line_id,
          qty: line.qty,
        })),
    },
    async () => {
      opened.value = false;
      emit("saved");
    },
  );
}
</script>

<template>
  <div
    v-if="receipt.lines.some((line) => positive(line.rejected_pending_qty))"
    class="form-actions"
  >
    <el-button v-if="can('purchase.receive')" :disabled="busy" @click="open"
      >拒收退回供应商</el-button
    >
    <span class="form-note">拒收品仍在保管中，实物交接后登记退回。</span>
  </div>
  <details v-if="receipt.rejected_handovers.length" class="inspection-history">
    <summary>查看拒收退回记录</summary>
    <div
      v-for="line in receipt.rejected_handovers"
      :key="line.id + line.receipt_line_id"
      class="posting-row"
    >
      <div>
        <b
          >{{ line.name }} · {{ line.lot_no }}：退回 {{ quantity(line.qty) }}
          {{ line.base_unit }}</b
        >
        <small
          >{{ localTime(line.created_at) }} · 交接凭据 {{ line.handover_ref }} ·
          接收方 {{ line.recipient }}</small
        >
        <small>{{ line.reason }}</small>
      </div>
    </div>
  </details>
  <el-dialog
    v-model="opened"
    title="拒收退回供应商"
    width="760px"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
  >
    <form class="business-form" @submit.prevent="save">
      <p>
        请核对已实际交接的商品、批号和数量。可以分次退回；填 0 的行本次不处理。
      </p>
      <p class="form-note">
        拒收品未入库，退回仅扣减保管数量。已入库商品请通过采购退货处理。保存后不能直接修改。
      </p>
      <div
        v-for="line in form.lines"
        :key="line.receipt_line_id"
        class="line-editor"
      >
        <div class="line-heading">
          <b>{{ line.name }} · {{ line.lot_no }}</b>
          <span
            >拒收待退 {{ quantity(line.pending) }} {{ line.base_unit }}</span
          >
        </div>
        <label
          >本次退回数量<input
            v-model="line.qty"
            required
            inputmode="decimal"
            pattern="[0-9]+([.][0-9]{1,6})?"
            maxlength="21"
        /></label>
      </div>
      <div class="form-grid">
        <label
          >交接凭据号<input
            v-model="form.handover_ref"
            required
            maxlength="120"
        /></label>
        <label
          >供应商接收人或承运方<input
            v-model="form.recipient"
            required
            maxlength="200"
        /></label>
      </div>
      <label
        >退回说明<input v-model="form.reason" required maxlength="200"
      /></label>
      <div class="form-actions">
        <el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="!form.lines.some((line) => positive(line.qty))"
          >确认实物已退回</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
