<script setup lang="ts">
import { closeRequest, resolveCloseChanges } from "./closeChanges";
</script>
<template>
  <el-dialog
    :model-value="!!closeRequest"
    title="关闭前保存修改"
    width="min(480px,94vw)"
    :close-on-click-modal="false"
    @update:model-value="resolveCloseChanges('cancel')"
  >
    <p>{{ closeRequest?.message }}</p>
    <p class="form-note">
      只有保存成功后才会关闭。未保存的普通表单仅保留在当前会话中。
    </p>
    <template #footer
      ><el-button @click="resolveCloseChanges('cancel')">取消关闭</el-button
      ><el-button @click="resolveCloseChanges('discard')">放弃修改</el-button
      ><el-button type="primary" @click="resolveCloseChanges('save')"
        >保存后关闭</el-button
      ></template
    >
  </el-dialog>
</template>
