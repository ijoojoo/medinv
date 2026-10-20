<script setup lang="ts">
defineProps<{ loading: boolean; error: string; hasData?: boolean }>();
defineEmits<{ retry: [] }>();
</script>
<template>
  <p v-if="error" class="wb-error" role="alert">
    {{ hasData ? "刷新失败，以下保留上次数据。" : "数据未能加载。" }}{{ error }}
    <button type="button" :disabled="loading" @click="$emit('retry')">
      重试
    </button>
  </p>
  <p v-else-if="loading" class="wb-note" role="status">
    {{ hasData ? "正在更新数据…" : "正在读取数据…" }}
  </p>
</template>
