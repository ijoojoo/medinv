<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ value: string; query: string }>();
const parts = computed(() => {
  const terms = [...new Set(props.query.trim().split(/\s+/).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!terms.length) return [{ text: props.value, match: false }];
  const pattern = new RegExp(terms.join("|"), "gi");
  const result: { text: string; match: boolean }[] = [];
  let start = 0;
  for (const match of props.value.matchAll(pattern)) {
    result.push({ text: props.value.slice(start, match.index), match: false });
    result.push({ text: match[0], match: true });
    start = match.index + match[0].length;
  }
  result.push({ text: props.value.slice(start), match: false });
  return result;
});
</script>
<template>
  <template v-for="(part, index) in parts" :key="index">
    <mark v-if="part.match">{{ part.text }}</mark
    ><template v-else>{{ part.text }}</template>
  </template>
</template>
