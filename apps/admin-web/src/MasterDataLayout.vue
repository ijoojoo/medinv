<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import MasterToolMenu from "./MasterToolMenu.vue";
import type { MasterTool } from "./masterTools";
defineProps<{
  tools: MasterTool[];
  label: string;
  disabled?: boolean;
  active?: boolean;
}>();
const emit = defineEmits<{ action: [key: string] }>();
const expanded = ref(false),
  overlay = ref(false),
  pendingAction = ref(""),
  toggle = ref<HTMLButtonElement>();
const media = window.matchMedia("(max-width: 1279px)");
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
        : ['data-workspace', { 'tools-expanded': expanded && !overlay }]
    "
    :aria-label="label"
  >
    <slot />
    <aside
      v-if="!disabled"
      class="data-toolbox"
      :aria-label="label + '工具箱'"
      @keydown.esc.stop.prevent="collapse"
    >
      <button
        ref="toggle"
        class="data-tool-toggle"
        :aria-expanded="expanded"
        :aria-label="expanded ? '收起工具箱' : '展开工具箱'"
        @click="expanded = !expanded"
      >
        <span aria-hidden="true">{{ expanded ? "›" : "‹" }}</span
        ><span>{{ expanded && !overlay ? "工具箱 · 收起" : "展开" }}</span>
      </button>
      <MasterToolMenu
        :tools="tools"
        :expanded="expanded && !overlay"
        @action="action"
      />
    </aside>
    <el-drawer
      v-if="!disabled && overlay && active !== false"
      :model-value="expanded"
      @update:model-value="expanded = $event"
      :title="label + '工具箱'"
      size="208px"
      append-to-body
      @close-auto-focus.prevent
      @closed="overlayClosed"
    >
      <MasterToolMenu :tools="tools" expanded @action="action" />
    </el-drawer>
  </section>
</template>
<style src="./masterDataLayout.css"></style>
