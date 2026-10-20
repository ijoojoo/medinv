<script setup lang="ts">
import { computed, ref } from "vue";
import { formatEntryNumber, type EntryColumn } from "./documentEntry";
const props = defineProps<{
  modelValue: string;
  column: EntryColumn;
  label: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const editing = ref(false);
const display = computed(() =>
  editing.value
    ? props.modelValue
    : formatEntryNumber(props.modelValue, props.column),
);
function focus(event: FocusEvent) {
  editing.value = true;
  // Install the raw value before the browser selects text or inserts a keystroke.
  const control = event.target as HTMLInputElement;
  control.value = props.modelValue;
  control.select();
}
</script>
<template>
  <input
    :value="display"
    :aria-label="label"
    :disabled="disabled"
    :tabindex="column.tab_stop ? 0 : -1"
    :title="'原值：' + modelValue"
    inputmode="decimal"
    maxlength="21"
    @focus="focus"
    @blur="editing = false"
    @input="
      emit('update:modelValue', ($event.target as HTMLInputElement).value)
    "
  />
</template>
