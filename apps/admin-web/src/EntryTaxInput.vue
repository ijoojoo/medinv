<script setup lang="ts">
import { computed, nextTick, ref, watch, useId } from "vue";
import { ElPopover } from "element-plus";
import { decimalMicros } from "./documentEntry";
const props = defineProps<{
  modelValue: string;
  disabled?: boolean;
  tabindex?: number;
  percent?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const input = ref<HTMLInputElement>();
const open = ref(false),
  editing = ref(false),
  active = ref(-1);
const id = useId();
const presets = ["0.13", "0.09", "0.06", "0.03", "0.01", "0"];
function focus(event: FocusEvent) {
  editing.value = true;
  const control = event.target as HTMLInputElement;
  control.value = props.modelValue;
  control.select();
}
const display = computed(() => {
  if (editing.value || !props.percent) return props.modelValue;
  const n = decimalMicros(props.modelValue.trim());
  if (n === null) return props.modelValue;
  const fraction = String(n % 10000n)
    .padStart(4, "0")
    .replace(/0+$/, "");
  return String(n / 10000n) + (fraction ? "." + fraction : "") + "%";
});
function keys(event: KeyboardEvent) {
  if (
    props.disabled ||
    event.isComposing ||
    event.keyCode === 229 ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey
  )
    return;
  if (
    !event.shiftKey &&
    (event.key === "ArrowDown" || event.key === "ArrowUp")
  ) {
    event.preventDefault();
    event.stopPropagation();
    const index =
      open.value && active.value >= 0
        ? active.value
        : presets.findIndex((v) => Number(v) === Number(props.modelValue));
    active.value =
      index < 0
        ? event.key === "ArrowDown"
          ? 0
          : presets.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + presets.length) %
          presets.length;
    open.value = true;
  } else if (event.key === "Escape" && open.value) {
    event.preventDefault();
    event.stopPropagation();
    open.value = false;
    active.value = -1;
  } else if (event.key === "Enter" || event.key === "Tab") {
    if (open.value && active.value >= 0 && !event.shiftKey)
      emit("update:modelValue", presets[active.value]!);
    open.value = false;
    active.value = -1;
    // Bubble to the grid so the same key confirms and advances once.
  }
}
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) {
      open.value = false;
      active.value = -1;
    }
  },
);
async function choose(value: string) {
  if (props.disabled) return;
  emit("update:modelValue", value);
  open.value = false;
  active.value = -1;
  input.value?.focus();
  await nextTick();
  input.value?.select();
}
</script>
<template>
  <div class="entry-tax-input">
    <input
      ref="input"
      :value="display"
      :tabindex="tabindex"
      :aria-expanded="open"
      :aria-controls="id"
      :aria-activedescendant="
        open && active >= 0 ? `${id}-${active}` : undefined
      "
      role="combobox"
      aria-autocomplete="none"
      :disabled="disabled"
      aria-label="税率（小数）"
      inputmode="decimal"
      maxlength="8"
      autocomplete="off"
      :title="
        percent
          ? '编辑按小数填写，如13%输入0.13；↑↓选择，Enter确认并跳格'
          : '↑↓选择，Enter确认并跳格，Esc取消选择'
      "
      @focus="focus"
      @blur="
        editing = false;
        open = false;
        active = -1;
      "
      @keydown="keys"
      @input="
        open = false;
        active = -1;
        emit('update:modelValue', ($event.target as HTMLInputElement).value);
      "
    />
    <el-popover
      v-model:visible="open"
      trigger="click"
      placement="bottom-end"
      :width="220"
      :disabled="disabled"
    >
      <template #reference
        ><button
          type="button"
          class="tax-presets-trigger"
          tabindex="-1"
          aria-label="选择常用税率"
          title="选择常用税率"
          :disabled="disabled"
        >
          ⌄
        </button></template
      >
      <div
        class="tax-presets"
        :id="id"
        @mousedown.prevent
        role="group"
        aria-label="常用税率"
        @keydown.esc.stop="
          open = false;
          input?.focus();
        "
      >
        <span>↑↓ 选择 · Enter 确认 · Esc 取消</span>
        <button
          v-for="(value, index) in presets"
          :key="value"
          :id="`${id}-${index}`"
          :class="{ active: active === index }"
          :aria-pressed="active === index"
          tabindex="-1"
          type="button"
          :disabled="disabled"
          @click="choose(value)"
        >
          {{ Number(value) * 100 }}%
        </button>
      </div>
    </el-popover>
  </div>
</template>
<style scoped>
.entry-tax-input {
  position: relative;
}
.entry-tax-input input {
  padding-right: 28px;
}
.tax-presets-trigger {
  position: absolute;
  right: 1px;
  top: 1px;
  bottom: 1px;
  width: 24px;
  border: 0;
  border-radius: 0 5px 5px 0;
  background: var(--ui-tint);
  color: var(--ui-muted);
}
.tax-presets {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}
.tax-presets span {
  grid-column: 1/-1;
  color: var(--ui-muted);
  font-size: 12px;
  margin-bottom: 4px;
}
.tax-presets button {
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  padding: 7px;
  background: var(--ui-tint);
  color: var(--ui-text);
}
.tax-presets button:hover,
.tax-presets button.active {
  background: var(--ui-soft);
}
</style>
