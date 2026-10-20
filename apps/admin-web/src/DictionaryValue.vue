<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from "vue";
import { ElPopover } from "element-plus";
import {
  matchesDictionaryValue,
  type CatalogDictionary,
} from "./catalogDictionaries";
defineOptions({ inheritAttrs: false });
const props = defineProps<{
  id?: string;
  dictionary: CatalogDictionary;
  modelValue: string;
  label: string;
  required?: boolean;
  disabled?: boolean;
  maxlength?: number;
  minlength?: number;
  pattern?: string;
  multiline?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const listId = useId();
const root = ref<HTMLElement>(),
  input = ref<HTMLInputElement>(),
  popup = ref<HTMLElement>();
const opened = ref(false),
  all = ref(false),
  composing = ref(false),
  index = ref(-1),
  width = ref(240);
let restoringFocus = false;
const active = computed(() =>
  props.dictionary.items
    .filter(
      (i) =>
        i.active &&
        matchesDictionaryValue(
          i.value,
          props.minlength,
          props.maxlength ?? props.dictionary.capacity,
          props.pattern,
        ),
    )
    .sort((a, b) => a.order - b.order),
);
const exact = computed(() =>
  active.value.some((i) => i.value === props.modelValue.trim()),
);
const candidates = computed(() =>
  all.value
    ? active.value
    : active.value.filter((i) =>
        i.value
          .toLocaleLowerCase()
          .includes(props.modelValue.trim().toLocaleLowerCase()),
      ),
);
const visible = computed(
  () =>
    opened.value &&
    !props.disabled &&
    !composing.value &&
    (all.value || !exact.value),
);
function close() {
  opened.value = false;
  index.value = -1;
}
function focus() {
  if (restoringFocus || props.disabled) return;
  all.value = false;
  opened.value = true;
  index.value = -1;
}
function focusInput() {
  restoringFocus = true;
  input.value?.focus();
  restoringFocus = false;
}
function toggle() {
  if (props.disabled || input.value?.matches(":disabled")) return;
  if (visible.value) close();
  else {
    all.value = true;
    opened.value = true;
    index.value = active.value.findIndex(
      (i) => i.value === props.modelValue.trim(),
    );
    void reveal();
  }
  focusInput();
}
function choose(value: string) {
  if (props.disabled || input.value?.matches(":disabled")) return;
  emit("update:modelValue", value);
  close();
  focusInput();
}
function blur(event: FocusEvent) {
  if (
    !root.value?.contains(event.relatedTarget as Node) &&
    !popup.value?.contains(event.relatedTarget as Node)
  )
    close();
}
function outside(event: PointerEvent) {
  if (
    !root.value?.contains(event.target as Node) &&
    !popup.value?.contains(event.target as Node)
  )
    close();
}
function resize() {
  width.value = Math.min(
    Math.max(root.value?.getBoundingClientRect().width || 240, 220),
    window.innerWidth - 24,
  );
}
watch(visible, (shown, _, cleanup) => {
  if (!shown) return;
  resize();
  const observer = new ResizeObserver(resize);
  if (root.value) observer.observe(root.value);
  document.addEventListener("pointerdown", outside);
  window.addEventListener("resize", resize);
  cleanup(() => {
    observer.disconnect();
    document.removeEventListener("pointerdown", outside);
    window.removeEventListener("resize", resize);
  });
});
watch(
  () => [
    props.modelValue,
    props.dictionary,
    props.disabled,
    props.minlength,
    props.maxlength,
    props.pattern,
  ],
  () => {
    index.value = -1;
    if (props.disabled) close();
  },
);
async function reveal() {
  await nextTick();
  popup.value
    ?.querySelector('[aria-selected="true"]')
    ?.scrollIntoView({ block: "nearest" });
}
async function keys(event: KeyboardEvent) {
  if (composing.value || event.isComposing || event.keyCode === 229) {
    if (event.key === "Enter") event.preventDefault();
    event.stopPropagation();
    return;
  }
  if (props.disabled || event.ctrlKey || event.metaKey || event.altKey) return;
  if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();
    if (!visible.value) {
      all.value = exact.value;
      opened.value = true;
      index.value = -1;
    }
    const count = candidates.value.length;
    if (count)
      index.value =
        index.value < 0
          ? event.key === "ArrowDown"
            ? 0
            : count - 1
          : (index.value + (event.key === "ArrowDown" ? 1 : -1) + count) %
            count;
    await reveal();
  } else if (event.key === "Enter" && visible.value) {
    event.preventDefault();
    event.stopPropagation();
    const item = candidates.value[index.value];
    if (item) choose(item.value);
    else close(); // Free text remains valid; Enter never silently replaces it.
  } else if (event.key === "Escape" && visible.value) {
    event.preventDefault();
    event.stopPropagation();
    close();
  } else if (event.key === "Tab") close();
}
const historical = computed(
  () =>
    !!props.modelValue &&
    !active.value.some((i) => i.value === props.modelValue),
);
function change(event: Event) {
  all.value = false;
  opened.value = true;
  index.value = -1;
  emit("update:modelValue", (event.target as HTMLInputElement).value);
}
function pick(event: Event) {
  const select = event.target as HTMLSelectElement;
  if (select.value) emit("update:modelValue", select.value);
  select.value = "";
}
</script>
<template>
  <select
    v-if="dictionary.mode === 'STRICT'"
    :id="id"
    :aria-label="label"
    :value="modelValue"
    :required="required"
    :disabled="disabled"
    @change="change"
  >
    <option value="">请选择</option>
    <option v-if="historical" :value="modelValue" disabled>
      {{ modelValue }}（当前历史值）
    </option>
    <option v-for="item in active" :key="item.value" :value="item.value">
      {{ item.value }}
    </option>
  </select>
  <template v-else-if="multiline">
    <textarea
      :id="id"
      :aria-label="label"
      :value="modelValue"
      :required="required"
      :disabled="disabled"
      :maxlength="maxlength ?? dictionary.capacity"
      :minlength="minlength"
      rows="2"
      @input="change"
    />
    <select
      v-if="active.length"
      :aria-label="`从字典选择${label}`"
      :disabled="disabled"
      value=""
      @change="pick"
    >
      <option value="">选择字典值填入，可继续补充</option>
      <option v-for="item in active" :key="item.value" :value="item.value">
        {{ item.value }}
      </option>
    </select>
  </template>
  <span v-else ref="root" class="dictionary-input" @focusout="blur">
    <ElPopover
      :visible="visible"
      :persistent="false"
      :width="width"
      placement="bottom-start"
      :show-arrow="false"
      :popper-options="{ strategy: 'fixed' }"
      popper-class="dictionary-suggestions"
    >
      <template #reference>
        <span class="dictionary-input-control">
          <input
            ref="input"
            :id="id"
            :aria-label="label"
            :value="modelValue"
            :required="required"
            :disabled="disabled"
            role="combobox"
            aria-autocomplete="list"
            :aria-expanded="visible"
            :aria-controls="visible ? listId : undefined"
            :aria-activedescendant="
              visible && index >= 0 ? `${listId}-${index}` : undefined
            "
            autocomplete="off"
            :maxlength="maxlength ?? dictionary.capacity"
            :minlength="minlength"
            :pattern="pattern"
            @input="change"
            @focus="focus"
            @keydown="keys"
            @compositionstart="composing = true"
            @compositionend="composing = false"
          />
          <button
            type="button"
            class="dictionary-toggle"
            :aria-label="`展开${label}字典`"
            :aria-expanded="visible"
            :disabled="disabled"
            @pointerdown.prevent
            @click="toggle"
          >
            <span aria-hidden="true">⌄</span>
          </button>
        </span>
      </template>
      <div ref="popup">
        <div class="dictionary-suggestions-heading">
          <strong>{{ label }}</strong
          ><span>{{ candidates.length }} 项{{ all ? "" : "匹配" }}</span>
        </div>
        <div
          :id="listId"
          role="listbox"
          :aria-label="`${label}字典候选`"
          class="dictionary-options"
        >
          <button
            v-for="(item, i) in candidates"
            :key="item.value"
            :id="`${listId}-${i}`"
            type="button"
            role="option"
            :aria-selected="index === i"
            @pointerdown.prevent
            @pointermove="index = i"
            @click="choose(item.value)"
          >
            <span>{{ item.value }}</span
            ><small v-if="item.value === modelValue.trim()">当前值 ✓</small
            ><small v-else-if="index === i">Enter</small>
          </button>
        </div>
        <p v-if="!candidates.length" class="dictionary-empty" role="status">
          未找到匹配项，可保留当前输入。
        </p>
        <div class="dictionary-suggestions-footer">
          ↑↓ 选择 · Enter 确认 · 也可自行输入
        </div>
      </div>
    </ElPopover>
  </span>
</template>
<style>
.dictionary-input,
.dictionary-input-control {
  display: block;
  width: 100%;
  min-width: 0;
  border: 0;
}
.dictionary-input-control {
  position: relative;
}
.dictionary-input-control > input {
  width: 100%;
  padding-right: 34px !important;
}
.dictionary-toggle {
  position: absolute;
  right: 3px;
  top: 3px;
  bottom: 3px;
  width: 28px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--ui-muted);
  font: inherit;
  cursor: pointer;
}
.dictionary-toggle:hover:not(:disabled),
.dictionary-toggle[aria-expanded="true"] {
  background: var(--ui-soft);
  color: var(--ui-accent);
}
.dictionary-toggle:disabled {
  cursor: default;
  opacity: 0.5;
}
.dictionary-suggestions.el-popper {
  padding: 6px;
  max-width: calc(100vw - 24px);
  border: 1px solid var(--ui-border);
  border-radius: 7px;
  box-shadow: 0 8px 24px rgb(25 50 80 / 16%);
  color: var(--ui-text);
}
.dictionary-suggestions-heading {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 5px 8px 8px;
  border-bottom: 1px solid var(--ui-border);
}
.dictionary-suggestions-heading strong {
  font-size: 13px;
}
.dictionary-suggestions-heading > span,
.dictionary-options small,
.dictionary-suggestions-footer,
.dictionary-empty {
  color: var(--ui-muted);
  font-size: 12px;
}
.dictionary-options {
  max-height: min(240px, 40dvh);
  overflow-y: auto;
  padding: 4px 0;
}
.dictionary-options > button {
  width: 100%;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: center;
  text-align: left;
  padding: 9px 10px;
  border: 0;
  border-radius: 4px;
  color: var(--ui-text);
  background: white;
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.dictionary-options > button > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.dictionary-options small {
  white-space: nowrap;
}
.dictionary-options > button:hover,
.dictionary-options > button[aria-selected="true"] {
  background: var(--ui-tint);
  color: var(--ui-accent);
}
.dictionary-suggestions-footer {
  border-top: 1px solid var(--ui-border);
  padding: 8px 8px 3px;
}
.dictionary-empty {
  margin: 0;
  padding: 14px 8px;
}
</style>
