<script setup lang="ts">
import { computed } from "vue";
import type { MasterNode } from "./productMaster";
const props = defineProps<{
  nodes: MasterNode[];
  parent: string | null;
  selected: string;
  expanded: string[];
  counts: Record<string, number>;
  depth?: number;
}>();
const emit = defineEmits<{ select: [id: string]; expand: [id: string] }>();
const children = computed(() =>
  props.nodes
    .filter((n) => n.parent_id === props.parent)
    .sort((a, b) => a.sort_order - b.sort_order),
);
</script>
<template>
  <ul class="master-tree" role="group">
    <li v-for="node in children" :key="node.node_id">
      <div
        class="master-tree-row"
        :class="{ selected: selected === node.node_id }"
        :style="{ paddingLeft: `${8 + (depth || 0) * 13}px` }"
      >
        <button
          v-if="nodes.some((n) => n.parent_id === node.node_id)"
          class="master-expand"
          :aria-label="`${expanded.includes(node.node_id) ? '收起' : '展开'}${node.name}`"
          :aria-expanded="expanded.includes(node.node_id)"
          @click.stop="emit('expand', node.node_id)"
        >
          {{ expanded.includes(node.node_id) ? "▾" : "▸" }}</button
        ><span v-else class="master-expand"></span
        ><button
          class="master-tree-label"
          :title="node.name"
          @click="emit('select', node.node_id)"
        >
          {{ node.name }}<small v-if="!node.enabled"> · 停用</small></button
        ><span class="master-tree-count">{{ counts[node.node_id] || 0 }}</span>
      </div>
      <MasterTree
        v-if="expanded.includes(node.node_id)"
        :nodes="nodes"
        :parent="node.node_id"
        :selected="selected"
        :expanded="expanded"
        :counts="counts"
        :depth="(depth || 0) + 1"
        @select="emit('select', $event)"
        @expand="emit('expand', $event)"
      />
    </li>
  </ul>
</template>
<style scoped>
.master-tree {
  list-style: none;
  padding: 0;
  margin: 0;
}
.master-tree-row {
  display: flex;
  align-items: center;
  min-height: 38px;
  padding-right: 10px;
  border-radius: 5px;
}
.master-tree-row.selected {
  background: #e7effa;
  color: #14558e;
}
.master-tree-row:hover {
  background: #f0f5fb;
}
.master-expand {
  width: 20px;
  flex: none;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
  padding: 0;
}
.master-tree-label {
  min-width: 0;
  flex: 1;
  text-align: left;
  border: 0;
  background: none;
  color: inherit;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 7px 0;
}
.master-tree-label small {
  color: #8995a2;
}
.master-tree-count {
  font-size: 11px;
  color: #7c90a4;
  margin-left: 6px;
}
</style>
