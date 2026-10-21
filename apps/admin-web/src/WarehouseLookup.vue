<script setup lang="ts">
import { computed, ref } from "vue";
import EntryReferenceLookup from "./EntryReferenceLookup.vue";
const props = withDefaults(
  defineProps<{
    modelValue?: string;
    entity: string;
    channel: "purchase" | "sale";
    label: string;
    required?: boolean;
    disabled?: boolean;
    qualifiedOnly?: boolean;
  }>(),
  { qualifiedOnly: true },
);
const emit = defineEmits<{
  "update:modelValue": [id: string];
  selected: [
    value: {
      id: string;
      name: string;
      code: string;
      eligible: boolean;
      credit_code?: string;
      state?: string;
    } | null,
  ];
}>();
const lookup = ref<InstanceType<typeof EntryReferenceLookup>>();
const ready = computed(() => lookup.value?.ready ?? false);
defineExpose({ ready });
</script>
<template>
  <EntryReferenceLookup
    ref="lookup"
    v-bind="props"
    kind="warehouse"
    @update:model-value="emit('update:modelValue', $event)"
    @selected="emit('selected', $event)"
  />
</template>
