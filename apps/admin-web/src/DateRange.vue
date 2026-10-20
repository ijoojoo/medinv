<script setup lang="ts">
import DateInput from "./DateInput.vue";
defineProps<{
  start: string;
  end: string;
  label?: string;
  max?: string;
  required?: boolean;
  query?: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{
  "update:start": [value: string];
  "update:end": [value: string];
}>();
</script>
<template>
  <label
    >{{ label || "日期" }}从<DateInput
      :model-value="start"
      :label="(label || '日期') + '从'"
      :required="required"
      :max="max"
      :disabled="disabled"
      :query="query"
      @update:model-value="emit('update:start', $event)"
  /></label>
  <label
    >{{ label || "日期" }}至<DateInput
      :model-value="end"
      :label="(label || '日期') + '至'"
      :required="required"
      :min="start"
      :max="max"
      :disabled="disabled"
      :query="query"
      @update:model-value="emit('update:end', $event)"
  /></label>
</template>
