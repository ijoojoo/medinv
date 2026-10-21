<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ lines: { qualification_notices?: string[] }[] }>();
const notices = computed(() => [
  ...new Set(props.lines.flatMap((line) => line.qualification_notices || [])),
]);
</script>
<template>
  <el-alert
    v-if="notices.length"
    type="warning"
    :closable="false"
    title="准入提醒 · 当前配置允许继续办理"
  >
    <p>以下为单据留存的检查结果；后续收发货仍会重新检查当前资质。</p>
    <ul>
      <li v-for="notice in notices" :key="notice">{{ notice }}</li>
    </ul>
  </el-alert>
</template>
