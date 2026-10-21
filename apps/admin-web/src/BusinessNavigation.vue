<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { navigationGroups } from "./navigation";
import { appearance } from "./appearance";
const props = defineProps<{
  modelValue: string;
  permissions: string[];
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [page: string] }>();
const symbols: Record<string, string> = {
  purchase: "↓",
  sales: "↑",
  inventory: "▤",
  quality: "◇",
  aftersales: "↩",
  data: "▧",
  finance: "¥",
  settings: "⚙",
  operations: "◎",
  insurance: "✚",
};
const root = ref<HTMLElement>(),
  popup = ref<HTMLElement>();
const opened = ref(""),
  left = ref(0),
  top = ref(48);
const groups = computed(() =>
  navigationGroups
    .map((g) => ({
      ...g,
      items: g.items.filter(
        (i) =>
          i.menu !== false &&
          (!i.permission || props.permissions.includes(i.permission)),
      ),
    }))
    .filter((g) => g.items.length),
);
const active = computed(() =>
  groups.value.find((g) => g.items.some((i) => i.page === props.modelValue)),
);
const menu = computed(() => groups.value.find((g) => g.id === opened.value));
let closeTimer: ReturnType<typeof setTimeout> | undefined;
let clicked = false;
let anchor: HTMLElement | undefined;
let resizeObserver: ResizeObserver | undefined;
let frame = 0;
function cancelClose() {
  clearTimeout(closeTimer);
}
function deferClose() {
  cancelClose();
  closeTimer = setTimeout(() => {
    if (!popup.value?.contains(document.activeElement)) close();
  }, 220);
}
function hover(id: string, event: PointerEvent) {
  if (props.disabled || event.pointerType !== "mouse") return;
  cancelClose();
  if (opened.value !== id) clicked = false;
  opened.value = id;
  position(event.currentTarget as HTMLElement);
}
function select(page: string) {
  if (!props.disabled) {
    close();
    emit("update:modelValue", page);
  }
}
function position(button: HTMLElement) {
  anchor = button;
  const r = button.getBoundingClientRect();
  left.value = Math.max(
    8,
    Math.min(
      appearance.value.layout === "sidebar" ? r.right + 6 : r.left,
      window.innerWidth - Math.min(410, window.innerWidth - 16) - 8,
    ),
  );
  top.value =
    appearance.value.layout === "sidebar"
      ? Math.max(8, Math.min(r.top, innerHeight - 240))
      : Math.max(8, Math.min(r.bottom + 4, innerHeight - 150));
}
watch(
  () => [appearance.value.layout, appearance.value.sidebarCollapsed],
  () => close(),
);
async function toggle(id: string, button: HTMLElement, focus = false) {
  cancelClose();
  if (opened.value === id && clicked && !focus) {
    close();
    return;
  }
  opened.value = id;
  clicked = true;
  position(button);
  if (focus) {
    await nextTick();
    popup.value?.querySelector<HTMLButtonElement>("button")?.focus();
  }
}
function close(focus = false) {
  cancelClose();
  clicked = false;
  const id = opened.value;
  opened.value = "";
  anchor = undefined;
  if (focus)
    root.value
      ?.querySelector<HTMLButtonElement>(`[data-menu-group="${id}"]`)
      ?.focus();
}
function outside(e: PointerEvent) {
  if (
    !root.value?.contains(e.target as Node) &&
    !popup.value?.contains(e.target as Node)
  )
    close();
}
function keys(e: KeyboardEvent) {
  if (e.isComposing || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === "Escape") {
    e.preventDefault();
    close(true);
    return;
  }
  const target = e.target as HTMLButtonElement;
  if (e.key === "Tab" && opened.value) {
    const entries = [
      ...(popup.value?.querySelectorAll<HTMLButtonElement>(
        "button:not(:disabled)",
      ) || []),
    ];
    const trigger = root.value?.querySelector<HTMLButtonElement>(
      `[data-menu-group="${opened.value}"]`,
    );
    if (target === trigger && !e.shiftKey) {
      e.preventDefault();
      entries[0]?.focus();
      return;
    }
    if (target === entries[0] && e.shiftKey) {
      e.preventDefault();
      trigger?.focus();
      return;
    }
    if (target === entries.at(-1) && !e.shiftKey) {
      e.preventDefault();
      const controls = [
        ...document.querySelectorAll<HTMLElement>(
          "button:not(:disabled), select:not(:disabled), input:not(:disabled), a[href]",
        ),
      ].filter((el) => el.offsetParent !== null && !popup.value?.contains(el));
      controls[controls.indexOf(trigger!) + 1]?.focus();
      close();
      return;
    }
  }
  if (e.key === "ArrowDown" && target.dataset.menuGroup) {
    e.preventDefault();
    void toggle(target.dataset.menuGroup, target, true);
    return;
  }
  if (
    ![
      "ArrowLeft",
      "ArrowRight",
      "ArrowDown",
      "ArrowUp",
      "Home",
      "End",
    ].includes(e.key)
  )
    return;
  const buttons = Array.from(
      target
        .closest("nav")
        ?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || [],
    ),
    index = buttons.indexOf(target);
  if (index < 0) return;
  e.preventDefault();
  const n =
    e.key === "Home"
      ? 0
      : e.key === "End"
        ? buttons.length - 1
        : (index +
            (["ArrowRight", "ArrowDown"].includes(e.key) ? 1 : -1) +
            buttons.length) %
          buttons.length;
  buttons[n]?.focus();
  buttons[n]?.scrollIntoView({ block: "nearest", inline: "nearest" });
}
watch(
  () => props.modelValue,
  () => close(),
);
onMounted(() => {
  document.addEventListener("pointerdown", outside);
  window.addEventListener("resize", resized);
  document.addEventListener("scroll", scrolled, true);
  resizeObserver = new ResizeObserver(resized);
  if (root.value) resizeObserver.observe(root.value);
});
function scrolled(e: Event) {
  if (!popup.value?.contains(e.target as Node)) resized();
}
function resized() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    if (anchor && opened.value) position(anchor);
  });
}
onUnmounted(() => {
  cancelClose();
  cancelAnimationFrame(frame);
  resizeObserver?.disconnect();
  document.removeEventListener("pointerdown", outside);
  document.removeEventListener("scroll", scrolled, true);
  window.removeEventListener("resize", resized);
});
</script>
<template>
  <div ref="root" class="business-navigation" @keydown="keys">
    <nav class="module-navigation" aria-label="主要功能">
      <button
        :disabled="disabled"
        :class="{ selected: modelValue === 'overview' }"
        :aria-current="modelValue === 'overview' ? 'page' : undefined"
        aria-label="企业工作台"
        @click="select('overview')"
      >
        <span class="module-symbol" aria-hidden="true">⌂</span
        ><span class="module-name">工作台</span>
      </button>
      <button
        v-for="group in groups"
        :key="group.id"
        :data-menu-group="group.id"
        :disabled="disabled"
        :class="{
          selected: active?.id === group.id,
          expanded: opened === group.id,
        }"
        :aria-expanded="opened === group.id"
        :aria-controls="'module-menu-' + group.id"
        :aria-label="group.name"
        :title="group.name"
        @click="toggle(group.id, $event.currentTarget as HTMLElement)"
        @pointerenter="hover(group.id, $event)"
        @pointerleave="deferClose"
      >
        <span class="module-symbol" aria-hidden="true">{{
          symbols[group.id] || "◧"
        }}</span
        ><span class="module-name">{{ group.name }}</span
        ><span class="module-chevron" aria-hidden="true">⌄</span>
      </button>
    </nav>
    <Teleport to="body">
      <nav
        v-if="menu"
        :id="'module-menu-' + menu.id"
        ref="popup"
        class="module-popup"
        :aria-label="menu.name + '功能'"
        :style="{ left: left + 'px', top: top + 'px' }"
        @keydown="keys"
        @pointerenter="cancelClose"
        @pointerleave="deferClose"
        @focusin="cancelClose"
        @focusout="
          (e) => {
            if (
              e.relatedTarget &&
              !popup?.contains(e.relatedTarget as Node) &&
              !root?.contains(e.relatedTarget as Node)
            )
              close();
          }
        "
      >
        <p>{{ menu.name }}</p>
        <div>
          <button
            v-for="item in menu.items"
            :key="item.page"
            :disabled="disabled"
            :aria-current="modelValue === item.page ? 'page' : undefined"
            @click="select(item.page)"
          >
            <span aria-hidden="true">{{ item.icon }}</span
            >{{ item.name }}<small aria-hidden="true">↗</small>
          </button>
        </div>
      </nav>
    </Teleport>
  </div>
</template>
<style scoped>
.business-navigation {
  flex: 1;
  min-width: 0;
}
.module-navigation {
  display: flex;
  align-items: stretch;
  height: var(--ui-header-height);
  overflow-x: auto;
  scrollbar-width: thin;
  gap: 2px;
}
.module-navigation button {
  flex: none;
  border: 0;
  padding: 0 13px;
  color: var(--ui-header-text);
  background: transparent;
  font: inherit;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}
.module-navigation button > .module-chevron {
  margin-left: 6px;
  font-size: 11px;
  opacity: 0.65;
}
.module-symbol {
  display: none;
}
.module-navigation button.selected {
  background: var(--ui-header-selected);
  color: var(--ui-header-text);
  box-shadow: inset 0 -3px var(--ui-header-text);
}
.module-navigation button.expanded {
  background: var(--ui-header-hover);
}
.module-navigation button:hover {
  background: var(--ui-header-hover);
  color: var(--ui-header-text);
}
.module-navigation button:focus-visible {
  outline: 2px solid var(--ui-header-text);
  outline-offset: -3px;
}
.module-popup {
  position: fixed;
  z-index: var(--ui-layer-navigation);
  max-height: calc(100dvh - v-bind('top + 8 + "px"'));
  overflow-y: auto;
  width: min(410px, calc(100vw - 16px));
  background: white;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  box-shadow: 0 12px 36px #18345324;
  padding: 16px;
}
.module-popup p {
  color: var(--ui-text);
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--ui-border);
}
.module-popup > div {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
}
.purchase-menu-groups section {
  min-width: 0;
}
.purchase-menu-groups section + section {
  border-left: 1px solid var(--ui-border);
  padding-left: 8px;
}
.purchase-menu-groups h4 {
  margin: 0 0 6px;
  padding: 0 10px;
  color: var(--ui-muted);
  font-size: 12px;
  font-weight: normal;
}
.purchase-menu-groups button {
  width: 100%;
  padding: 9px 8px;
  gap: 7px;
}
.module-popup button {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  padding: 12px 10px;
  color: var(--ui-text);
  font: inherit;
  font-size: 14px;
  text-align: left;
}
.module-popup button > span {
  color: var(--ui-accent);
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 6px;
  background: var(--ui-soft);
}
.module-popup button > small {
  margin-left: auto;
  color: #9baba6;
}
.module-popup button:hover,
.module-popup button[aria-current] {
  background: var(--ui-soft);
  color: var(--ui-accent);
}
.module-popup button:focus-visible {
  outline: 2px solid var(--ui-accent);
}
button:disabled {
  opacity: 0.5;
  cursor: wait;
}
</style>
