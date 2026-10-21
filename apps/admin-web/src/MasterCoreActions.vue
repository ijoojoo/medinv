<script setup lang="ts">
import type { MasterTool } from "./masterTools";
defineProps<{ tools: MasterTool[]; keys: string[] }>();
const emit = defineEmits<{ action: [key: string] }>();
</script>
<template>
  <div class="master-core-actions" aria-label="主要操作">
    <el-button
      v-for="tool in tools.filter((t) => keys.includes(t.key))"
      :key="tool.key"
      :data-core-action="tool.key"
      :type="
        ['new', 'location', 'revise'].includes(tool.key) ? 'primary' : undefined
      "
      :disabled="!!tool.disabled"
      :aria-disabled="!!tool.disabled"
      :title="tool.disabled || tool.label"
      @click="emit('action', tool.key)"
      >{{ tool.label }}</el-button
    >
  </div>
</template>
<style scoped>
.master-core-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.master-core-actions .el-button {
  margin-left: 0;
}
</style>
