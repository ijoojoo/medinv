<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import MasterDataLayout from "./MasterDataLayout.vue";
import type { MasterTool } from "./masterTools";
const AppearanceSettings = defineAsyncComponent(
  () => import("./AppearanceSettings.vue"),
);
const props = defineProps<{
  label: string;
  contextKey: string;
  disabled?: boolean;
  active?: boolean;
  tools?: MasterTool[];
  help?: string[];
}>();
const emit = defineEmits<{ action: [key: string] }>();
const dialog = ref("");
watch(
  () => [props.contextKey, props.active, props.disabled],
  () => {
    dialog.value = "";
  },
);
const tools = computed<MasterTool[]>(() => [
  ...(props.tools || []),
  { key: "display", label: "显示与操作设置", icon: "◧", group: "辅助操作" },
  { key: "help", label: "本页使用说明", icon: "?", group: "辅助操作" },
  { key: "link", label: "复制页面链接", icon: "⧉", group: "辅助操作" },
]);
async function action(key: string) {
  if (key === "display" || key === "help") dialog.value = key;
  else if (key === "link") {
    try {
      await navigator.clipboard.writeText(window.location.href);
      ElMessage.success("页面链接已复制；未保存的编辑内容不包含在链接中");
    } catch {
      ElMessage.error("未能访问剪贴板，请复制浏览器地址栏中的链接");
    }
  } else emit("action", key);
}
</script>
<template>
  <div class="page-extension-host">
    <MasterDataLayout
      plain
      :label="label"
      :context-key="contextKey"
      :active="active"
      :disabled="disabled"
      :tools="tools"
      @action="action"
    >
      <div class="extension-content"><slot /></div>
    </MasterDataLayout>
    <el-dialog
      :model-value="!!dialog && !disabled && active !== false"
      @update:model-value="!$event && (dialog = '')"
      :title="dialog === 'display' ? '显示与操作设置' : label + ' · 使用说明'"
      :width="dialog === 'display' ? 'min(800px, 94vw)' : 'min(560px, 94vw)'"
      append-to-body
    >
      <AppearanceSettings v-if="dialog === 'display'" />
      <div v-else class="extension-help">
        <p v-for="line in help || []" :key="line">{{ line }}</p>
        <p>
          扩展功能提供低频辅助工具。新增、查询、保存和业务办理操作位于主区域；按钮是否可用取决于当前权限和业务状态。
        </p>
        <p>
          显示设置立即生效并保存在当前浏览器。复制页面链接不会包含尚未保存的编辑内容；打开链接仍需要相应权限。
        </p>
      </div>
    </el-dialog>
  </div>
</template>
<style scoped>
.page-extension-host {
  min-width: 0;
}
.extension-help {
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.extension-help p {
  margin: 0 0 16px;
}
</style>
