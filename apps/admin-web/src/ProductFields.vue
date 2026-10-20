<script setup lang="ts">
import DateInput from "./DateInput.vue";
import { computed } from "vue";
import DictionaryValue from "./DictionaryValue.vue";
import {
  effectiveFields,
  validationPatterns,
  validationOptions,
  type ProductFieldRule,
} from "./productFieldRules";
const props = defineProps<{
  fields: ProductFieldRule[];
  prefix?: string;
  contextValues?: Record<string, string>;
}>();
const model = defineModel<Record<string, string>>({ required: true });
const effective = computed(() =>
  effectiveFields(props.fields, {
    ...props.contextValues,
    ...model.value,
  }).filter((f) => f.shown),
);
function change(key: string, event: Event, select = false) {
  const value = (event.target as HTMLInputElement).value;
  const next = { ...model.value, [key]: select && !value ? "UNKNOWN" : value };
  const previous = model.value.device_class;
  if (
    key === "device_class" &&
    previous &&
    previous !== "UNKNOWN" &&
    value &&
    (previous === "I") !== (value === "I")
  )
    next.approval_number = "";
  model.value = next;
}
</script>
<template>
  <div class="full-width form-grid product-profile-fields">
    <label
      v-for="field in effective"
      :key="field.key"
      :data-field="field.key"
      :class="{ 'full-width': field.capacity > 200 }"
    >
      <span
        >{{ field.label
        }}<span v-if="field.required" class="required-mark" aria-hidden="true">
          *</span
        ></span
      >
      <select
        v-if="field.kind === 'select'"
        :aria-label="(prefix || '') + field.label"
        :value="model[field.key] === 'UNKNOWN' ? '' : model[field.key]"
        :required="field.required"
        @change="change(field.key, $event, true)"
      >
        <option
          v-for="(name, value) in field.options"
          :key="value"
          :value="value === 'UNKNOWN' ? '' : value"
        >
          {{ name }}
        </option>
      </select>
      <DictionaryValue
        v-else-if="field.dictionary"
        :model-value="model[field.key] || ''"
        :dictionary="field.dictionary"
        :label="(prefix || '') + field.label"
        :required="field.required"
        :maxlength="field.max_length"
        :minlength="field.min_length"
        :pattern="validationPatterns[field.validator]"
        :multiline="['storage_conditions', 'quality_note'].includes(field.key)"
        @update:model-value="model = { ...model, [field.key]: $event }"
      />
      <textarea
        v-else-if="['storage_conditions', 'quality_note'].includes(field.key)"
        :value="model[field.key]"
        :aria-label="(prefix || '') + field.label"
        :required="field.required"
        :minlength="field.min_length"
        :maxlength="field.max_length"
        rows="2"
        @input="change(field.key, $event)"
      />
      <DateInput
        v-else-if="field.kind === 'date'"
        :model-value="model[field.key] || ''"
        :label="(prefix || '') + field.label"
        :required="field.required"
        :min="field.key === 'valid_until' ? model.valid_from : undefined"
        @update:model-value="model = { ...model, [field.key]: $event }"
      />
      <input
        v-else
        :value="model[field.key]"
        :aria-label="(prefix || '') + field.label"
        type="text"
        :required="field.required"
        :min="field.key === 'valid_until' ? model.valid_from : undefined"
        :minlength="field.kind === 'text' ? field.min_length : undefined"
        :maxlength="field.kind === 'text' ? field.max_length : undefined"
        :pattern="
          field.kind === 'text'
            ? validationPatterns[field.validator]
            : undefined
        "
        :title="
          field.validator !== 'NONE'
            ? validationOptions[field.validator]
            : undefined
        "
        @input="change(field.key, $event)"
      />
      <small v-if="field.required_when.length">{{
        field.required ? "当前分类要求填写" : "符合联动条件时必填"
      }}</small>
      <small v-if="!field.visible">此字段已有内容，保留显示供核对。</small>
      <small
        v-if="field.key === 'approval_number' && model.device_class === 'I'"
        >填写产品备案号；不是生产或经营企业备案号。</small
      >
    </label>
  </div>
</template>
<style scoped>
.required-mark {
  color: #b74e36;
}
small {
  font-weight: 400;
  color: #6b766f;
}
</style>
