<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import type { EntryPackages } from "./documentEntry";
const props = defineProps<{
  sku: string;
  modelValue: string;
  disabled?: boolean;
  label?: string;
  compact?: boolean;
  tabindex?: number;
  cache?: Map<string, Promise<EntryPackages>>;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
  failure: [error: unknown];
}>();
const choices = ref<
  { id: string; unit_name: string; numerator: string; denominator: string }[]
>([]);
const base = ref("基本单位"),
  loading = ref(false);
const selected = computed(() =>
  choices.value.find((p) => p.id === props.modelValue),
);
let epoch = 0;
onUnmounted(() => epoch++);
watch(
  () => props.sku,
  async (sku, previousSku) => {
    const current = ++epoch;
    if (previousSku !== undefined) emit("update:modelValue", "");
    choices.value = [];
    base.value = "基本单位";
    loading.value = false;
    if (!sku) return;
    loading.value = true;
    try {
      let pending = props.cache?.get(sku);
      if (!pending) {
        pending = request<EntryPackages>(
          `/api/v1/catalog/skus/${sku}/packaging?approved_only=true`,
        );
        props.cache?.set(sku, pending);
      }
      const result = await pending;
      if (current === epoch) {
        choices.value = result.items;
        base.value = result.base_unit;
      }
    } catch (e) {
      props.cache?.delete(sku);
      if (current === epoch) emit("failure", e);
    } finally {
      if (current === epoch) loading.value = false;
    }
  },
  { immediate: true },
);
</script>
<template>
  <label
    ><span v-if="!compact">{{ label || "交易单位" }}</span
    ><select
      :aria-label="label || '交易单位'"
      :value="modelValue"
      :tabindex="tabindex"
      :disabled="disabled || loading || !sku"
      @change="
        emit('update:modelValue', ($event.target as HTMLSelectElement).value)
      "
    >
      <option value="">{{ base }}（基本单位）</option>
      <option
        v-if="modelValue && !loading && !selected"
        :value="modelValue"
        :tabindex="tabindex"
        disabled
      >
        原包装已不可用，请重新选择交易单位并核对数量与单价
      </option>
      <option v-for="p in choices" :key="p.id" :value="p.id">
        {{ p.unit_name }} · 1 {{ p.unit_name }} = {{ p.numerator
        }}{{ p.denominator === "1" ? "" : "/" + p.denominator }} {{ base }}
      </option></select
    ><small v-if="!compact" class="cell-sub"
      >数量与单价按 {{ selected?.unit_name || base }} 填写</small
    ></label
  >
</template>
