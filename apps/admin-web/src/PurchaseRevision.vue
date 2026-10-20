<script setup lang="ts">
import PartyLookup from "./PartyLookup.vue";
import { onMounted, ref } from "vue";
import DocumentEntryGrid from "./DocumentEntryGrid.vue";
import {
  loadOptions,
  useCommand,
  type PurchaseOptions,
  type PackageSnapshot,
} from "./business";
export interface EditablePurchase {
  order: {
    id: string;
    entity_id: string;
    version: string;
    supplier_id: string;
    warehouse_id: string;
  };
  lines: {
    sku_id: string;
    qty: string;
    trade_qty: string | null;
    unit_price: string;
    tax_rate: string;
    package_snapshot: PackageSnapshot | null;
  }[];
}
const props = defineProps<{
  record: EditablePurchase;
  options: PurchaseOptions;
}>();
const emit = defineEmits<{ failure: [error: unknown]; close: []; saved: [] }>();
const choices = ref(props.options);
const loading = ref(true);
const entry = ref<InstanceType<typeof DocumentEntryGrid>>();
const form = ref({
  supplier_id: props.record.order.supplier_id,
  warehouse_id: props.record.order.warehouse_id,
  reason: "",
  lines: props.record.lines.map((item) => ({
    client_key: crypto.randomUUID(),
    sku_id: item.sku_id,
    packaging_id: item.package_snapshot?.packaging_id || "",
    qty: item.trade_qty || item.qty,
    unit_price: item.unit_price,
    tax_rate: String(Number(item.tax_rate)),
  })),
});
const { busy, execute } = useCommand((e) => emit("failure", e));
onMounted(async () => {
  try {
    choices.value = await loadOptions<PurchaseOptions>(
      `/api/v1/purchasing/options?entity_id=${props.record.order.entity_id}&include_skus=false&include_parties=false`,
    );
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
});
async function save() {
  const rows = entry.value?.prepare();
  if (!rows) return;
  if (loading.value) return;
  await execute(
    `/api/v1/purchase-orders/${props.record.order.id}/revise`,
    {
      ...form.value,
      expected_version: props.record.order.version,
      lines: rows.map((item) => ({
        sku_id: item.sku_id,
        qty: item.qty,
        unit_price: item.unit_price,
        tax_rate: item.tax_rate,
        packaging_id: item.packaging_id || undefined,
      })),
    },
    () => emit("saved"),
  );
}
</script>
<template>
  <el-dialog
    :model-value="true"
    title="修改采购单"
    width="min(1320px, 96vw)"
    class="business-dialog"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    @close="emit('close')"
  >
    <form v-loading="loading" class="business-form" @submit.prevent="save">
      <fieldset class="document-entry-fields" :disabled="busy || loading">
        <p class="form-note">
          保存后采购单转回待审核，审核通过前不能登记到货。请核对交易单位、数量与含税单价，并说明变更原因。
        </p>
        <div class="form-grid">
          <label
            >供应商<PartyLookup
              v-model="form.supplier_id"
              :entity="record.order.entity_id"
              channel="purchase"
              label="供应商"
              required
              :disabled="busy || loading"
          /></label>
          <label
            >收货仓库<select v-model="form.warehouse_id" required>
              <option v-for="w in choices.warehouses" :key="w.id" :value="w.id">
                {{ w.name }}
              </option>
            </select></label
          >
        </div>
        <DocumentEntryGrid
          ref="entry"
          v-model="form.lines"
          :entity="record.order.entity_id"
          channel="purchase"
          :disabled="busy || loading"
          @failure="emit('failure', $event)"
        />
        <label
          >变更原因<textarea
            v-model="form.reason"
            required
            maxlength="500"
            rows="3"
          />
        </label>
        <div class="form-actions">
          <el-button :disabled="busy" @click="emit('close')">取消</el-button
          ><el-button type="primary" native-type="submit" :loading="busy"
            >保存变更并待审</el-button
          >
        </div>
      </fieldset>
    </form>
  </el-dialog>
</template>
