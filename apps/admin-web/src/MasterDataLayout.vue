<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, useId } from "vue";
import MasterToolMenu from "./MasterToolMenu.vue";
import type { MasterTool } from "./masterTools";
const props = defineProps<{
  tools: MasterTool[];
  label: string;
  disabled?: boolean;
  active?: boolean;
  plain?: boolean;
  contextKey?: string;
}>();
const emit = defineEmits<{ action: [key: string] }>();
const menuId = useId();
const expanded = ref(false),
  overlay = ref(false),
  pendingAction = ref(""),
  toggle = ref<HTMLButtonElement>();
const media = window.matchMedia("(max-width: 1279px)");
watch(
  () => props.contextKey,
  () => {
    expanded.value = false;
    pendingAction.value = "";
  },
);
const sync = () => {
  overlay.value = media.matches;
};
function collapse() {
  expanded.value = false;
  toggle.value?.focus();
}
function action(key: string) {
  if (overlay.value && expanded.value) {
    pendingAction.value = key;
    expanded.value = false;
    return;
  }
  emit("action", key);
}
function overlayClosed() {
  const key = pendingAction.value;
  pendingAction.value = "";
  toggle.value?.focus();
  if (key) emit("action", key);
}
onMounted(() => {
  sync();
  media.addEventListener("change", sync);
});
onUnmounted(() => media.removeEventListener("change", sync));
</script>
<template>
  <section
    :class="
      disabled
        ? 'data-layout-embedded'
        : [
            plain ? 'extension-workspace' : 'data-workspace',
            { 'tools-expanded': expanded && !overlay },
          ]
    "
    :aria-label="label"
  >
    <slot />
    <aside
      v-if="!disabled"
      class="data-toolbox"
      :aria-label="label + '扩展功能'"
      @keydown.esc.stop.prevent="collapse"
    >
      <button
        ref="toggle"
        class="data-tool-toggle"
        :aria-expanded="expanded"
        :aria-controls="menuId"
        :aria-label="expanded ? '收起扩展功能' : '展开扩展功能'"
        @click="expanded = !expanded"
      >
        <span aria-hidden="true">{{ expanded ? "›" : "‹" }}</span
        ><span>{{ expanded && !overlay ? "扩展功能 · 收起" : "扩展" }}</span>
      </button>
      <MasterToolMenu
        v-if="expanded && !overlay"
        :id="menuId"
        :tools="tools"
        :expanded="expanded && !overlay"
        @action="action"
      />
    </aside>
    <el-drawer
      v-if="!disabled && overlay && active !== false"
      :model-value="expanded"
      @update:model-value="expanded = $event"
      :title="label + '扩展功能'"
      size="208px"
      append-to-body
      @close-auto-focus.prevent
      @closed="overlayClosed"
    >
      <MasterToolMenu :id="menuId" :tools="tools" expanded @action="action" />
    </el-drawer>
  </section>
</template>
<style src="./masterDataLayout.css"></style>
