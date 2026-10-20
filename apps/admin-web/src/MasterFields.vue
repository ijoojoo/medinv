<script setup lang="ts">
import DateInput from "./DateInput.vue";
import { computed, reactive } from "vue";
import { can } from "./business";
import MasterReferenceInput from "./MasterReferenceInput.vue";
import DictionaryValue from "./DictionaryValue.vue";
import { validationPatterns } from "./productFieldRules";
import {
  fieldsFor,
  summary,
  type SchemaResponse,
  type MasterValues,
  type MasterIssue,
  type Organization,
} from "./productMaster";
const props = defineProps<{
  data: SchemaResponse;
  profile: string;
  tab: string;
  values: MasterValues;
  issues?: MasterIssue[];
  existing?: boolean;
  unitLocked?: boolean;
  disabled?: boolean;
  organizations?: Organization[];
}>();
const emit = defineEmits<{
  change: [key: string, value: unknown];
  relation: [key: string];
}>();
const fields = computed(() =>
  fieldsFor(props.data, props.profile, props.values),
);
const dictionaries = computed(() =>
  Object.fromEntries(
    Object.entries(props.data.dictionaries || {}).map(([key, dictionary]) => [
      key,
      {
        ...dictionary,
        items: dictionary.items.filter(
          (i) => !["—", "未知", "不适用"].includes(i.value),
        ),
      },
    ]),
  ),
);
const slots = computed(() =>
  props.data.payload.slots
    .filter((s) => s.tab === props.tab)
    .sort((a, b) => a.position - b.position)
    .map((s) => ({
      ...s,
      field: fields.value.find((f) => f.key === s.field_key)!,
    })),
);
function label(key: string, current: string) {
  if (key === "barcodes" && current === "条码与包装") return "条码（基本单位）";
  if (key === "reference_prices" && current === "参考价格（按现有 SKU）")
    return "参考价格";
  return current;
}
function readonly(key: string) {
  const f = fields.value.find((f) => f.key === key)!;
  return (
    props.disabled ||
    !f.applicable ||
    f.read_only ||
    !can(f.write_permission) ||
    !can(f.read_permission) ||
    f.lifecycle === "INACTIVE" ||
    (props.existing && key === "code") ||
    (props.unitLocked && key === "base_unit")
  );
}
function input(key: string, event: Event, type: string) {
  const v = (event.target as HTMLInputElement).value;
  emit("change", key, v === "" ? null : type === "boolean" ? v === "true" : v);
}
const dateErrors = reactive<Record<string, string>>({});
const hint = (key: string) =>
  dateErrors[key] || props.issues?.find((i) => i.field === key)?.message;
</script>
<template>
  <div class="master-form-container">
    <div class="master-fixed-grid" :data-tab="tab">
      <div
        v-for="s in slots"
        :key="s.slot_id"
        class="master-fixed-slot"
        :class="{
          'not-applicable': !s.field.applicable,
          'master-price-slot':
            s.field_key === 'reference_prices' &&
            s.field.applicable &&
            !!$slots.reference_prices,
          'has-error': hint(s.field_key),
        }"
        :data-slot="s.slot_id"
        :data-field="s.field_key"
      >
        <label
          :for="`master-${s.slot_id}`"
          :title="label(s.field_key, s.field.label)"
          ><span
            v-if="s.field.required && s.field.applicable"
            class="master-required"
            >*</span
          >{{ label(s.field_key, s.field.label) }}</label
        >
        <input
          v-if="!can(s.field.read_permission)"
          :id="`master-${s.slot_id}`"
          value="无查看权限"
          disabled
        />
        <input
          v-else-if="s.field.unconfirmed"
          :id="`master-${s.slot_id}`"
          value="待核实：先明确子类型 / 管理类别"
          disabled
        />
        <input
          v-else-if="!s.field.applicable"
          :id="`master-${s.slot_id}`"
          value="本品类不适用"
          disabled
        />
        <MasterReferenceInput
          v-else-if="
            [
              'regulatory_subject',
              'manufacturer',
              'service_provider',
              'product_certificates',
            ].includes(s.field_key)
          "
          :id="`master-${s.slot_id}`"
          :field="s.field_key"
          :profile="profile"
          :values="values"
          :disabled="readonly(s.field_key)"
          :details-disabled="disabled || !can(s.field.read_permission)"
          @change="emit('change', s.field_key, $event)"
          @manage="emit('relation', s.field_key)"
        />
        <slot
          v-else-if="$slots[s.field_key]"
          :name="s.field_key"
          :id="`master-${s.slot_id}`"
          :disabled="readonly(s.field_key)"
        />
        <button
          v-else-if="s.field.value_type === 'collection'"
          :id="`master-${s.slot_id}`"
          class="master-summary"
          :disabled="disabled"
          :title="summary(values[s.field_key])"
          @click="emit('relation', s.field_key)"
        >
          <span>{{ summary(values[s.field_key]) }}</span
          ><span class="master-link">管理 ↗</span>
        </button>
        <select
          v-else-if="s.field.value_type === 'select'"
          :id="`master-${s.slot_id}`"
          :value="values[s.field_key] ?? ''"
          :disabled="readonly(s.field_key)"
          @change="input(s.field_key, $event, 'select')"
        >
          <option value="">请选择</option>
          <option v-for="o in s.field.options" :key="o.value" :value="o.value">
            {{ o.label }}
          </option>
        </select>
        <select
          v-else-if="s.field.value_type === 'boolean'"
          :id="`master-${s.slot_id}`"
          :value="
            values[s.field_key] === null || values[s.field_key] === undefined
              ? ''
              : String(values[s.field_key])
          "
          :disabled="readonly(s.field_key)"
          @change="input(s.field_key, $event, 'boolean')"
        >
          <option value="">未填写</option>
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
        <select
          v-else-if="s.field.value_type === 'reference'"
          :id="`master-${s.slot_id}`"
          :value="values[s.field_key] ?? ''"
          :disabled="readonly(s.field_key)"
          @change="input(s.field_key, $event, 'reference')"
        >
          <option value="">请选择科室</option>
          <option
            v-for="o in (organizations || []).filter(
              (o) => o.kind === 'DEPARTMENT',
            )"
            :value="o.id"
            :key="o.id"
          >
            {{ o.name }}
          </option>
        </select>
        <DateInput
          v-else-if="s.field.value_type === 'date'"
          :id="`master-${s.slot_id}`"
          :model-value="String(values[s.field_key] ?? '')"
          :label="s.field.label"
          :disabled="readonly(s.field_key)"
          compact
          @update:model-value="emit('change', s.field_key, $event || null)"
          @validity="dateErrors[s.field_key] = $event"
        />
        <DictionaryValue
          v-else-if="dictionaries[s.field_key]"
          :id="`master-${s.slot_id}`"
          :dictionary="dictionaries[s.field_key]"
          :model-value="String(values[s.field_key] ?? '')"
          :label="s.field.label"
          :disabled="readonly(s.field_key)"
          :maxlength="
            Math.min(
              s.field.max_length || 500,
              dictionaries[s.field_key].capacity,
            )
          "
          :minlength="s.field.min_length || 0"
          :pattern="validationPatterns[s.field.validator || 'NONE']"
          @update:model-value="emit('change', s.field_key, $event || null)"
        />
        <input
          v-else
          :id="`master-${s.slot_id}`"
          :value="values[s.field_key] ?? ''"
          type="text"
          :inputmode="
            ['decimal', 'integer'].includes(s.field.value_type)
              ? 'decimal'
              : 'text'
          "
          :maxlength="s.field_key === 'code' ? 60 : s.field.max_length || 500"
          :disabled="readonly(s.field_key)"
          :placeholder="
            s.field_key === 'code' ? '留空按品类规则自动生成' : '未填写'
          "
          @input="input(s.field_key, $event, s.field.value_type)"
        />
        <div
          class="master-feedback"
          :title="hint(s.field_key) || s.field.semantic_definition"
        >
          {{
            hint(s.field_key) ||
            (!s.field.applicable
              ? "保留固定位置"
              : s.field.read_only
                ? "只读字段"
                : s.field_key === "record_status"
                  ? "与质量审核、组织经营开关分别管理"
                  : s.field_key === "barcodes"
                    ? `基本单位：${values.base_unit || "未填写"} · 其他包装在下方维护`
                    : [
                          "regulatory_subject",
                          "manufacturer",
                          "service_provider",
                          "product_certificates",
                        ].includes(s.field_key)
                      ? "直接输入 · ↑↓选择 · 保存时补充字典"
                      : dictionaries[s.field_key]
                        ? dictionaries[s.field_key].mode === "STRICT"
                          ? "仅选用启用的字典值 · 历史值可保留"
                          : "可输入或选择字典值 · 按品类规则校验"
                        : s.field.help_text || "")
          }}
        </div>
      </div>
    </div>
    <div v-if="!slots.length" class="master-empty-tab">
      当前没有配置此分组字段，页签与页面骨架继续保留。
    </div>
  </div>
</template>
<style>
.master-form-container {
  container-type: inline-size;
  width: 100%;
}
.master-fixed-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 18px;
  padding: 16px 20px;
}
.master-fixed-slot {
  height: 94px;
  min-width: 0;
  display: grid;
  grid-template-rows: 24px 36px 22px;
  align-content: start;
}
.master-fixed-slot label {
  font-size: 13px;
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #3d536b;
  line-height: 24px;
}
.master-fixed-slot.not-applicable > label {
  color: #8592a0;
  font-weight: 400;
}
.master-fixed-slot.not-applicable .master-feedback {
  color: #929ca7;
}
.master-price-slot {
  grid-column: 1 / -1;
  height: auto;
  grid-template-rows: 24px auto 22px;
}
.master-fixed-slot input,
.master-fixed-slot select,
.master-summary {
  box-sizing: border-box;
  width: 100%;
  height: 36px;
  border: 1px solid #ccd8e5;
  border-radius: 5px;
  background: var(--el-bg-color, #fff);
  padding: 0 10px;
  font: inherit;
  font-size: 14px;
  min-width: 0;
  color: #233b53;
}
.master-fixed-slot input:focus,
.master-fixed-slot select:focus,
.master-summary:focus {
  outline: 2px solid #699fde;
  outline-offset: 1px;
}
.master-fixed-slot input:disabled,
.master-fixed-slot select:disabled {
  background: #f3f6f9;
  color: #81909d;
  border-style: dashed;
}
.master-summary {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  text-align: left;
}
.master-summary > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.master-link {
  flex: none;
  color: #2167a8;
  font-size: 12px;
}
.master-feedback {
  font-size: 11px;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #81909d;
}
.master-required,
.has-error .master-feedback {
  color: #c0473a;
}
.has-error input,
.has-error select {
  border-color: #d88875;
}
.master-empty-tab {
  padding: 50px 20px;
  text-align: center;
  color: #8995a2;
}
@container (max-width:850px) {
  .master-fixed-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@container (max-width:500px) {
  .master-fixed-grid {
    grid-template-columns: minmax(0, 1fr);
    padding: 12px;
  }
}
</style>
