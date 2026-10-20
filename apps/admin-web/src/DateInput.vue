<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  useAttrs,
  useId,
  watch,
} from "vue";
import { ElPopover } from "element-plus";
import {
  businessToday,
  calendarDate,
  daysInMonth,
  moveDay,
  moveMonth,
  parseDate,
  weekday,
} from "./dates";
import { registerDateControl } from "./dateControls";
defineOptions({ inheritAttrs: false });
const props = withDefaults(
  defineProps<{
    modelValue?: string | null;
    label?: string;
    required?: boolean;
    disabled?: boolean;
    readonly?: boolean;
    min?: string;
    max?: string;
    disabledDate?: (date: string) => boolean;
    compact?: boolean;
    query?: boolean;
    advanceOnEnter?: boolean;
    clearable?: boolean;
  }>(),
  { clearable: true },
);
const emit = defineEmits<{
  "update:modelValue": [value: string];
  validity: [error: string];
}>();
const attrs = useAttrs(),
  uid = useId();
const root = ref<HTMLElement>(),
  input = ref<HTMLInputElement>(),
  panel = ref<HTMLElement>();
const raw = ref(props.modelValue || ""),
  error = ref(""),
  checked = ref(false),
  opened = ref(false),
  composing = ref(false);
const cursor = ref(businessToday());
const label = computed(
  () =>
    props.label ||
    String(
      attrs["aria-label"] ||
        root.value?.closest("label")?.childNodes[0]?.textContent?.trim() ||
        "日期",
    ),
);
const locked = computed(() => props.disabled || props.readonly);
const selected = computed(() => parseDate(raw.value).value);
const year = computed(() => Number(cursor.value.slice(0, 4))),
  month = computed(() => Number(cursor.value.slice(5, 7)));
const title = computed(() => `${year.value}年${month.value}月`);
const weeks = computed(() => {
  const lead = weekday(calendarDate(year.value, month.value, 1)),
    total = daysInMonth(year.value, month.value);
  return Array.from({ length: Math.ceil((lead + total) / 7) }, (_, row) =>
    Array.from({ length: 7 }, (_, col) => {
      const day = row * 7 + col - lead + 1;
      return day > 0 && day <= total
        ? calendarDate(year.value, month.value, day)
        : "";
    }),
  );
});
const allowed = (date: string) => !parseDate(date, props).error;
function setError(value: string) {
  error.value = value;
  emit("validity", value);
}
function commit(value: string) {
  raw.value = value;
  if (input.value) input.value.value = value;
  emit("update:modelValue", value);
}
function confirm() {
  checked.value = true;
  // Read the actual editor, even when confirmation was triggered before blur.
  const result = parseDate(input.value?.value ?? raw.value, props);
  setError(result.error);
  return result;
}
function blur() {
  if (locked.value || composing.value) return;
  const result = confirm();
  if (!result.error) commit(result.value);
}
function change(e: Event) {
  raw.value = (e.target as HTMLInputElement).value;
  emit("update:modelValue", raw.value);
  if (checked.value) setError(parseDate(raw.value, props).error);
}
watch(
  () => props.modelValue,
  (value) => {
    raw.value = value || "";
    if (checked.value) setError(parseDate(raw.value, props).error);
  },
  { flush: "sync" },
);
watch(
  () => [props.min, props.max, props.required, props.disabledDate],
  () => {
    if (checked.value) setError(parseDate(raw.value, props).error);
  },
);
watch(locked, (value) => {
  if (value) opened.value = false;
});
function focusDay() {
  panel.value
    ?.querySelector<HTMLButtonElement>(`[data-day="${cursor.value}"]`)
    ?.focus();
}
async function show() {
  if (locked.value) return;
  cursor.value = selected.value || businessToday();
  opened.value = true;
  await nextTick();
}
let returnToInput = false;
function close(restore = false) {
  returnToInput = restore;
  opened.value = false;
}
async function afterClose() {
  // Let the library release its nested focus trap before restoring the editor.
  await nextTick();
  if (returnToInput && !locked.value) input.value?.focus();
  returnToInput = false;
}
function choose(value: string) {
  if (locked.value || (value && !allowed(value))) return;
  commit(value);
  checked.value = true;
  setError(parseDate(value, props).error);
  close(true);
}
function inputKey(e: KeyboardEvent) {
  if (e.isComposing || composing.value || e.keyCode === 229) {
    if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
    }
    return;
  }
  if (e.key === "Escape" && opened.value) {
    e.stopPropagation();
    close(true);
  }
  if (e.key === "ArrowDown" && e.altKey) {
    e.preventDefault();
    void show();
  }
  if (props.advanceOnEnter && e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    blur();
    if (error.value) {
      e.preventDefault();
      e.stopPropagation();
    }
    return;
  }
  if (e.key === "Enter" || (e.key === "Tab" && props.advanceOnEnter)) {
    e.preventDefault();
    e.stopPropagation();
    if (props.query || input.value?.closest("[data-date-query]"))
      input.value?.form?.requestSubmit();
    else {
      blur();
      if (props.advanceOnEnter && (!error.value || e.shiftKey))
        input.value?.dispatchEvent(
          new CustomEvent("document-grid-advance", {
            bubbles: true,
            detail: { reverse: e.shiftKey },
          }),
        );
    }
  }
}
async function calendarKey(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    close(true);
    return;
  }
  const inDay = (e.target as HTMLElement).hasAttribute("data-day");
  let next = cursor.value;
  if (e.key === "PageUp" || e.key === "PageDown")
    next = moveMonth(
      next,
      (e.key === "PageUp" ? -1 : 1) * (e.shiftKey ? 12 : 1),
    );
  else if (!inDay) return;
  else if (e.key === "ArrowLeft") next = moveDay(next, -1);
  else if (e.key === "ArrowRight") next = moveDay(next, 1);
  else if (e.key === "ArrowUp") next = moveDay(next, -7);
  else if (e.key === "ArrowDown") next = moveDay(next, 7);
  else if (e.key === "Home") next = moveDay(next, -weekday(next));
  else if (e.key === "End") next = moveDay(next, 6 - weekday(next));
  else return;
  e.preventDefault();
  cursor.value = next;
  await nextTick();
  focusDay();
}
function outside(e: PointerEvent) {
  if (
    !root.value?.contains(e.target as Node) &&
    !panel.value?.contains(e.target as Node)
  )
    close();
}
function focusOut(e: FocusEvent) {
  if (
    e.relatedTarget &&
    !root.value?.contains(e.relatedTarget as Node) &&
    !panel.value?.contains(e.relatedTarget as Node)
  )
    close();
}
let unregister: (() => void) | undefined;
onMounted(() => {
  unregister = registerDateControl({
    element: () => root.value,
    confirm,
    commit,
    disabled: () => !!locked.value || !!input.value?.matches(":disabled"),
    focus: () => {
      input.value?.dispatchEvent(new Event("date-invalid", { bubbles: true }));
      void nextTick(() => {
        input.value?.focus();
        input.value?.scrollIntoView({ block: "nearest" });
      });
    },
  });
  document.addEventListener("pointerdown", outside);
});
onUnmounted(() => {
  unregister?.();
  document.removeEventListener("pointerdown", outside);
});
defineExpose({ confirm, commit, focus: () => input.value?.focus() });
</script>
<template>
  <span
    ref="root"
    class="date-control"
    :class="{ 'date-invalid': error, 'date-compact': compact }"
    @focusout="focusOut"
  >
    <span class="date-input-line">
      <input
        v-bind="attrs"
        ref="input"
        type="text"
        :value="raw"
        :disabled="disabled"
        :readonly="readonly"
        :aria-label="label"
        :aria-required="required || undefined"
        :aria-invalid="!!error"
        :aria-describedby="error ? uid + '-error' : undefined"
        placeholder="YYYYMMDD / YYYY-MM-DD"
        autocomplete="off"
        @input="change"
        @blur="blur"
        @keydown="inputKey"
        @compositionstart="composing = true"
        @compositionend="
          composing = false;
          change($event);
        "
      />
      <ElPopover
        :visible="opened"
        :persistent="false"
        :width="316"
        placement="bottom-end"
        :fallback-placements="['top-end', 'bottom-start', 'top-start']"
        :popper-options="{ strategy: 'fixed' }"
        :show-arrow="false"
        popper-class="date-popover"
        @after-enter="focusDay"
        @after-leave="afterClose"
      >
        <template #reference
          ><button
            type="button"
            class="date-trigger"
            :disabled="locked"
            :aria-label="label + '，打开日历'"
            :aria-expanded="opened"
            aria-haspopup="dialog"
            @click="opened ? close(true) : show()"
          >
            ▦
          </button></template
        >
        <div
          ref="panel"
          role="dialog"
          :aria-label="label + '日历'"
          class="date-calendar"
          @keydown="calendarKey"
          @focusout="focusOut"
        >
          <div class="date-calendar-heading">
            <button
              type="button"
              aria-label="上一年"
              :disabled="year === 1"
              @click="cursor = moveMonth(cursor, -12)"
            >
              «
            </button>
            <button
              type="button"
              aria-label="上一月"
              :disabled="year === 1 && month === 1"
              @click="cursor = moveMonth(cursor, -1)"
            >
              ‹
            </button>
            <strong aria-live="polite">{{ title }}</strong>
            <button
              type="button"
              aria-label="下一月"
              :disabled="year === 9999 && month === 12"
              @click="cursor = moveMonth(cursor, 1)"
            >
              ›
            </button>
            <button
              type="button"
              aria-label="下一年"
              :disabled="year === 9999"
              @click="cursor = moveMonth(cursor, 12)"
            >
              »
            </button>
          </div>
          <table
            role="grid"
            :aria-label="title + '，方向键选日，PageUp / PageDown 翻月'"
          >
            <thead>
              <tr>
                <th
                  v-for="day in ['一', '二', '三', '四', '五', '六', '日']"
                  :key="day"
                  scope="col"
                >
                  {{ day }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(week, index) in weeks" :key="index">
                <td
                  v-for="(day, column) in week"
                  :key="column"
                  :aria-selected="day ? day === selected : undefined"
                >
                  <button
                    v-if="day"
                    type="button"
                    :data-day="day"
                    :aria-label="day"
                    :aria-disabled="!allowed(day)"
                    :aria-current="day === businessToday() ? 'date' : undefined"
                    :tabindex="day === cursor ? 0 : -1"
                    :class="{
                      selected: day === selected,
                      unavailable: !allowed(day),
                    }"
                    @focus="cursor = day"
                    @click="choose(day)"
                  >
                    {{ Number(day.slice(-2)) }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="date-calendar-actions">
            <button
              type="button"
              :disabled="!allowed(businessToday())"
              @click="choose(businessToday())"
            >
              今天</button
            ><button v-if="clearable" type="button" @click="choose('')">
              清空</button
            ><button type="button" @click="close(true)">关闭</button>
          </div>
        </div>
      </ElPopover>
    </span>
    <small
      v-if="error"
      :id="uid + '-error'"
      class="date-feedback"
      :class="{ 'visually-hidden': compact }"
      :title="error"
      role="alert"
      >{{ error }}</small
    >
  </span>
</template>
<style>
.date-control {
  display: inline-flex;
  flex-direction: column;
  min-width: 0;
  width: 100%;
  position: relative;
}
.date-input-line {
  display: flex;
  align-items: stretch;
  position: relative;
  min-width: 0;
}
.date-input-line > input {
  width: 100%;
  min-width: 0;
  height: var(--ui-control-height, 36px);
  padding-right: 32px !important;
  box-sizing: border-box;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  font: inherit;
}
.date-trigger {
  position: absolute;
  right: 1px;
  top: 1px;
  bottom: 1px;
  width: 30px;
  border: 0;
  border-radius: 0 5px 5px 0;
  background: transparent;
  color: var(--ui-accent);
  font-size: 20px;
  padding: 0;
}
.date-trigger:hover {
  background: var(--ui-soft);
}
.date-invalid input {
  border-color: var(--ui-danger) !important;
}
.date-feedback {
  position: absolute;
  top: 100%;
  left: 0;
  max-width: 100%;
  font-size: 12px;
  color: var(--ui-danger);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  line-height: 20px;
}
.date-popover {
  max-width: calc(100vw - 16px);
  padding: 12px !important;
  max-height: calc(100dvh - 16px);
  overflow: auto;
}
.date-calendar {
  color: var(--ui-text);
  font:
    14px/1.5 "Segoe UI",
    "Microsoft YaHei UI",
    sans-serif;
}
.date-calendar button {
  font: inherit;
  color: var(--ui-text);
  border: 0;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  min-height: 32px;
}
.date-calendar button:hover,
.date-calendar button:focus-visible {
  background: var(--ui-soft);
  outline: 2px solid var(--ui-accent);
  outline-offset: -2px;
}
.date-calendar-heading {
  display: flex;
  align-items: center;
  gap: 4px;
}
.date-calendar-heading strong {
  flex: 1;
  text-align: center;
}
.date-calendar-heading button {
  width: 28px;
  font-size: 18px;
}
.date-calendar table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  margin: 8px 0;
}
.date-calendar th {
  color: var(--ui-muted);
  font-weight: 400;
  padding: 4px 0;
}
.date-calendar td {
  padding: 2px;
  text-align: center;
  height: 36px;
}
.date-calendar td button {
  width: 100%;
}
.date-calendar .selected {
  background: var(--ui-accent);
  color: white;
}
.date-calendar [aria-current="date"] {
  box-shadow: inset 0 0 0 1px var(--ui-accent);
}
.date-calendar .unavailable,
.date-calendar button:disabled {
  color: #777;
  text-decoration: line-through;
  cursor: not-allowed;
}
.date-calendar-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--ui-border);
}
.date-calendar-actions button {
  padding: 4px 12px;
}
@media (pointer: coarse) {
  .date-calendar button {
    min-height: 44px;
  }
  .date-trigger {
    width: 44px;
  }
  .date-input-line > input {
    padding-right: 46px !important;
  }
}
</style>
