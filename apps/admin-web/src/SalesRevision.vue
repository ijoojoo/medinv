<script setup lang="ts">
import PartyLookup from "./PartyLookup.vue";
import SaleStockPanel from "./SaleStockPanel.vue";
import { onMounted, ref } from "vue";
import DocumentEntryGrid from "./DocumentEntryGrid.vue";
import { loadOptions, useCommand } from "./business";
import type { SalesOptions, SalesDetailData } from "./sales";
const props = defineProps<{ record: SalesDetailData }>();
const emit = defineEmits<{ failure: [error: unknown]; close: []; saved: [] }>();
const choices = ref<SalesOptions>({ customers: [], warehouses: [], skus: [] });
const loading = ref(true);
const entry = ref<InstanceType<typeof DocumentEntryGrid>>();
const form = ref({
  customer_id: props.record.order.customer_id,
  warehouse_id: props.record.order.warehouse_id,
  delivery_address: props.record.order.delivery_address,
  minimum_shelf_days: props.record.order.minimum_shelf_days,
  credit_days: props.record.order.credit_days,
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
    choices.value = await loadOptions<SalesOptions>(
      `/api/v1/selling/options?entity_id=${props.record.order.entity_id}&include_skus=false&include_parties=false`,
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
    `/api/v1/sales-orders/${props.record.order.id}/revise`,
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
    title="修改销售单"
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
          保存后释放原信用占用，销售单转回待审核；重新审核通过后才能分配发货。请核对客户、交易单位、数量、价格和送货约定。
        </p>
        <div class="form-grid">
          <label
            >客户<PartyLookup
              v-model="form.customer_id"
              :entity="record.order.entity_id"
              channel="sale"
              label="客户"
              required
              :disabled="busy || loading"
          /></label>
          <label
            >发货仓库<select v-model="form.warehouse_id" required>
              <option v-for="w in choices.warehouses" :key="w.id" :value="w.id">
                {{ w.name }}
              </option>
            </select></label
          >
        </div>
        <div class="form-grid">
          <label
            >最低剩余效期（天）<input
              v-model.number="form.minimum_shelf_days"
              type="number"
              min="0"
              max="3650"
              step="1"
              required
          /></label>
          <label
            >账期（天）<input
              v-model.number="form.credit_days"
              type="number"
              min="0"
              max="365"
              step="1"
              required
          /></label>
        </div>
        <label
          >收货地址<input
            v-model="form.delivery_address"
            required
            maxlength="500"
        /></label>
        <DocumentEntryGrid
          ref="entry"
          v-model="form.lines"
          :entity="record.order.entity_id"
          channel="sale"
          :disabled="busy || loading"
          @failure="emit('failure', $event)"
        />
        <SaleStockPanel
          channel="wholesale"
          :entity="record.order.entity_id"
          :warehouse="form.warehouse_id"
          :skus="form.lines.map((row) => row.sku_id)"
          :minimum-shelf-days="form.minimum_shelf_days"
          :disabled="busy"
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
