<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { request, session } from "./api";
import {
  entryQuantity,
  type EntryChannel,
  type EntryLine,
  type EntryPackages,
} from "./documentEntry";
import { microsText, packagingReferencePrice } from "./entryTrade";

const props = defineProps<{
  row?: EntryLine;
  entity: string;
  channel: EntryChannel;
  disabled?: boolean;
  active?: boolean;
  pending?: boolean;
  packageRevision?: number;
}>();
const emit = defineEmits<{
  adopt: [key: string, value: string];
  packagesLoaded: [sku: string, data: EntryPackages];
}>();
type Prices = {
  base_unit: string;
  values: { purchase_price: string | null; wholesale_price: string | null };
};
const prices = ref<Prices>(),
  packages = ref<EntryPackages>();
const loading = ref(false),
  priceError = ref(false),
  packError = ref(false);
const canRead = computed(() =>
  session.value?.permissions.includes("catalog.read"),
);
let epoch = 0,
  controller: AbortController | undefined;
const scope = computed(() => [
  session.value?.tenant_id,
  session.value?.context_generation,
  session.value?.permission_version,
  props.entity,
  props.channel,
  props.row?.client_key,
  props.row?.sku_id,
  props.row?.packaging_id,
  props.packageRevision,
  props.active,
  canRead.value,
]);
async function load() {
  const current = ++epoch;
  controller?.abort();
  prices.value = undefined;
  packages.value = undefined;
  priceError.value = packError.value = false;
  loading.value = false;
  if (!props.row?.sku_id || props.active === false || !canRead.value) return;
  loading.value = true;
  controller = new AbortController();
  const signal = controller.signal,
    sku = props.row.sku_id;
  const results = await Promise.allSettled([
    request<Prices>(`/api/v1/catalog/skus/${sku}/prices`, { signal }),
    request<EntryPackages>(
      `/api/v1/catalog/skus/${sku}/packaging?approved_only=true`,
      { signal },
    ),
  ]);
  if (current !== epoch) return;
  const [priceResult, packResult] = results;
  if (priceResult.status === "fulfilled") prices.value = priceResult.value;
  else priceError.value = true;
  if (packResult.status === "fulfilled") {
    packages.value = packResult.value;
    emit("packagesLoaded", sku, packResult.value);
  } else packError.value = true;
  loading.value = false;
}
watch(scope, () => void load(), { immediate: true, flush: "sync" });
onUnmounted(() => {
  ++epoch;
  controller?.abort();
});
const pack = computed(() =>
  packages.value?.items.find((p) => p.id === props.row?.packaging_id),
);
const base = computed(
  () =>
    prices.value?.base_unit ||
    packages.value?.base_unit ||
    props.row?.product?.base_unit ||
    "基本单位",
);
const unit = computed(() =>
  props.row?.packaging_id ? pack.value?.unit_name || "原包装" : base.value,
);
const priceName = computed(() =>
  props.channel === "purchase" ? "参考采购价" : "参考批发价",
);
const raw = computed(() =>
  props.channel === "purchase"
    ? prices.value?.values.purchase_price
    : prices.value?.values.wholesale_price,
);
const basePrice = computed(() => packagingReferencePrice(raw.value));
const converted = computed(() =>
  props.row?.packaging_id
    ? pack.value &&
      packagingReferencePrice(
        raw.value,
        pack.value.numerator,
        pack.value.denominator,
      )
    : basePrice.value,
);
const quantity = computed(() => {
  const row = props.row,
    p = pack.value;
  if (!row?.packaging_id || !p) return "";
  if (!row.qty.trim() || !row.product) return "";
  const result = entryQuantity(row.qty, row.product.qty_scale, base.value, p);
  return result.micros !== undefined
    ? `折合 ${microsText(result.micros)} ${base.value}`
    : result.message;
});
const adoptable = computed(
  () =>
    !!converted.value &&
    !!props.row?.product?.eligible &&
    !props.row?.unit_price.trim() &&
    !props.pending &&
    !props.row?.search_text &&
    !props.disabled &&
    props.active !== false &&
    !loading.value,
);
function adopt() {
  if (adoptable.value && props.row && converted.value)
    emit("adopt", props.row.client_key, converted.value.value);
}
</script>

<template>
  <div
    v-if="row?.sku_id"
    class="entry-trade-hint"
    aria-label="当前行单位与参考价格"
  >
    <span
      >数量、单价按 <b>{{ unit }}</b> 填写</span
    >
    <span v-if="pack"
      >{{ pack.denominator }} {{ pack.unit_name }} ＝ {{ pack.numerator }}
      {{ base }}</span
    >
    <span v-if="quantity">{{ quantity }}</span>
    <template v-if="canRead">
      <span v-if="loading">正在读取参考信息…</span>
      <span v-else-if="priceError">参考价读取失败</span>
      <template v-else-if="basePrice">
        <span
          >商品资料 · {{ priceName }} ¥{{ basePrice.value }}／{{
            base
          }}（含税）</span
        >
        <span v-if="row.packaging_id && converted"
          >换算 ¥{{ converted.value }}／{{ unit
          }}<small v-if="converted.rounded">（四舍五入至6位小数）</small></span
        >
        <span v-if="row.packaging_id && pack && !converted"
          >换算价超出单据可录入范围</span
        >
        <button
          type="button"
          :disabled="!adoptable"
          @click="adopt"
          :title="
            row.unit_price.trim()
              ? '已有单价保留；清空单价后可采用参考价'
              : '仅填入当前行的空白单价，可继续修改'
          "
        >
          填入参考价
        </button>
        <span v-if="row.unit_price.trim()">已有单价保留</span>
      </template>
      <span v-else>{{ priceName }}未设置</span>
      <span v-if="!loading && row.packaging_id && !pack"
        >{{
          packError ? "包装读取失败" : "原包装已不可用"
        }}，请核对交易单位</span
      >
      <button
        type="button"
        class="refresh"
        :disabled="loading || disabled"
        @click="load"
      >
        刷新参考信息
      </button>
    </template>
  </div>
</template>

<style scoped>
.entry-trade-hint {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 12px;
  align-items: center;
  line-height: 20px;
  overflow-wrap: anywhere;
}
.entry-trade-hint b {
  color: var(--ui-text);
}
.entry-trade-hint button {
  border: 1px solid var(--ui-border);
  border-radius: 4px;
  padding: 1px 7px;
  font: inherit;
  color: var(--ui-accent);
  background: white;
  cursor: pointer;
}
.entry-trade-hint button:disabled {
  color: var(--ui-muted);
  opacity: 0.65;
  cursor: default;
}
.entry-trade-hint .refresh {
  border-color: transparent;
  background: transparent;
}
</style>
