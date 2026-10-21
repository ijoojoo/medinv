<script setup lang="ts">
import type { MasterTool } from "./masterTools";
import { ElMessage } from "element-plus";
defineProps<{ tools: MasterTool[]; expanded: boolean }>();
const emit = defineEmits<{ action: [key: string] }>();
const groups = ["资料维护", "关联查阅", "辅助操作"];
function activate(tool: MasterTool) {
  if (tool.disabled) ElMessage.info(tool.disabled);
  else emit("action", tool.key);
}
</script>
<template>
  <div class="master-tool-menu" :class="{ expanded }">
    <template v-for="group in groups" :key="group">
      <section
        v-if="tools.some((t) => t.group === group && (expanded || t.short))"
      >
        <h3 v-if="expanded">{{ group }}</h3>
        <template
          v-for="tool in tools.filter(
            (t) => t.group === group && (expanded || t.short),
          )"
          :key="tool.key"
        >
          <button
            type="button"
            :data-tool="tool.key"
            :aria-label="tool.label"
            :aria-disabled="!!tool.disabled"
            :title="tool.disabled || tool.label"
            @click="activate(tool)"
          >
            <span aria-hidden="true" class="tool-icon">{{ tool.icon }}</span>
            <span>{{ expanded ? tool.label : tool.short }}</span>
            <span v-if="tool.disabled" class="visually-hidden">{{
              tool.disabled
            }}</span>
          </button>
        </template>
      </section>
    </template>
  </div>
</template>
