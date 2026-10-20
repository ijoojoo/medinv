<script setup lang="ts">
import {
  allowedHomeViews,
  homeView,
  selectHomeView,
  type HomeView,
} from "./workbench";
defineProps<{ cards?: boolean }>();
</script>
<template>
  <div v-if="cards" class="home-view-choices">
    <button
      v-for="v in allowedHomeViews"
      :key="v.value"
      type="button"
      :aria-pressed="homeView === v.value"
      @click="selectHomeView(v.value)"
    >
      <span class="home-view-preview" :class="v.value" aria-hidden="true"
        ><i></i><i></i><i></i><i></i
      ></span>
      <strong>{{ v.name }}</strong
      ><small>{{ v.note }}</small>
    </button>
  </div>
  <label v-else class="home-view-select"
    >工作台方案
    <select
      :value="homeView"
      @change="
        selectHomeView(($event.target as HTMLSelectElement).value as HomeView)
      "
    >
      <option v-for="v in allowedHomeViews" :key="v.value" :value="v.value">
        {{ v.name }}
      </option>
    </select>
  </label>
</template>
<style scoped>
.home-view-select {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--ui-muted);
  white-space: nowrap;
}
select {
  border: 1px solid var(--ui-border);
  border-radius: 7px;
  padding: 7px 10px;
  background: white;
  color: var(--ui-text);
  font: inherit;
  min-width: 130px;
}
.home-view-choices {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
button {
  text-align: left;
  padding: 15px;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  background: white;
  color: var(--ui-text);
}
button[aria-pressed="true"] {
  border-color: var(--ui-accent);
  box-shadow: 0 0 0 1px var(--ui-accent);
  background: var(--ui-tint);
}
strong,
small {
  display: block;
  margin-top: 10px;
}
small {
  color: var(--ui-muted);
  line-height: 1.6;
}
.home-view-preview {
  display: grid;
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 22px 48px 30px;
  gap: 6px;
  background: var(--ui-bg);
  padding: 9px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
}
i {
  background: var(--ui-soft);
  border: 1px solid var(--ui-accent-line);
  border-radius: 3px;
}
i:first-child {
  grid-column: 1/-1;
  background: var(--ui-accent);
  opacity: 0.6;
}
i:last-child {
  grid-column: 1/-1;
}
.risk {
  grid-template-columns: 1.5fr 1fr;
}
.operations {
  grid-template-columns: 3fr 1fr;
}
.operations i:nth-child(2) {
  grid-row: span 2;
}
.operations i:last-child {
  grid-column: 2;
}
@media (max-width: 680px) {
  .home-view-choices {
    grid-template-columns: 1fr;
  }
  .home-view-preview {
    height: 100px;
  }
}
</style>
