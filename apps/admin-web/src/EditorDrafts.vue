<script setup lang="ts">
import { ref } from "vue";
import type { EntryChannel } from "./documentEntry";
import DraftBrowser from "./DraftBrowser.vue";
defineProps<{ channel: EntryChannel; entity: string }>();
defineEmits<{ failure: [unknown] }>();
const opened = ref(false);
</script>
<template>
  <el-button :disabled="!entity" @click="opened = true">我的草稿</el-button>
  <DraftBrowser v-model="opened" :entity="entity" :channel="channel" />
</template>
