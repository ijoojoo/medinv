<script setup lang="ts">
import { computed } from "vue";
import { activeEntity } from "./entityScope";
import { homeView, homeViews, allowedHomeViews } from "./workbench";
import type { WorkQueueFilters, WorkbenchVisit } from "./workQueue";
import HomeViewPicker from "./HomeViewPicker.vue";
import WorkbenchInsights from "./WorkbenchInsights.vue";
import WorkbenchOperations from "./WorkbenchOperations.vue";
import WorkbenchRisk from "./WorkbenchRisk.vue";
import "./workbench.css";
const props = defineProps<{
  active: boolean;
  modelValue: WorkQueueFilters | null;
}>();
const emit = defineEmits<{
  open: [WorkbenchVisit];
  "update:modelValue": [WorkQueueFilters];
}>();
const title = computed(
  () => homeViews.find((v) => v.value === homeView.value)!.title,
);
const available = (key: string) =>
  allowedHomeViews.value.some((v) => v.value === key);
</script>
<template>
  <section
    class="workbench"
    aria-label="企业工作台方案"
    :data-home-view="homeView"
  >
    <header class="wb-titlebar">
      <div>
        <h2>{{ title }}</h2>
      </div>
      <HomeViewPicker />
    </header>
    <p v-if="!activeEntity" class="wb-empty">请先选择经营主体。</p>
    <template v-else>
      <div v-if="available('insights')" v-show="homeView === 'insights'">
        <WorkbenchInsights :active="props.active && homeView === 'insights'" />
      </div>
      <div v-if="available('risk')" v-show="homeView === 'risk'">
        <WorkbenchRisk
          :active="props.active && homeView === 'risk'"
          @open="emit('open', $event)"
        />
      </div>
      <div v-show="homeView === 'operations'">
        <WorkbenchOperations
          :active="props.active && homeView === 'operations'"
          :model-value="modelValue"
          @update:model-value="emit('update:modelValue', $event)"
          @open="emit('open', $event)"
        />
      </div>
    </template>
  </section>
</template>
