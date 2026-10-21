<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{
  items: { label: string; identity: string; value: string }[];
  type: string;
  unit: string;
  metricLabel: string;
  states: Record<string, string>;
}>();
const values = computed(() =>
  props.items.map((x) => Math.max(0, Number(x.value))),
);
const max = computed(() => Math.max(1, ...values.value));
const total = computed(() => values.value.reduce((a, b) => a + b, 0));
const colors = [
  "#2563eb",
  "#0d9488",
  "#7c3aed",
  "#d97706",
  "#db2777",
  "#0891b2",
  "#65a30d",
  "#475569",
];
const name = (label: string) => props.states[label] || label;
const points = computed(() =>
  props.items
    .map(
      (_, i) =>
        `${50 + (i * 700) / Math.max(1, props.items.length - 1)},${270 - (values.value[i] / max.value) * 220}`,
    )
    .join(" "),
);
const slices = computed(() => {
  let offset = 0;
  return props.items.map((item, i) => {
    const size = total.value ? (values.value[i] / total.value) * 100 : 0;
    const part = { ...item, size, offset, color: colors[i % colors.length] };
    offset += size;
    return part;
  });
});
</script>
<template>
  <div
    class="report-chart"
    role="figure"
    :aria-label="metricLabel + '统计图，数据来自全部筛选结果'"
  >
    <p v-if="!items.length" class="chart-empty">
      没有可绘制的数据，请调整查询条件。
    </p>
    <template v-else>
      <div v-if="type === 'bar'" class="report-bars">
        <div
          v-for="(item, index) in items"
          :key="item.identity + item.label"
          class="report-bar-row"
        >
          <span :title="name(item.label)">{{ name(item.label) }}</span>
          <div class="report-bar-track">
            <i
              :style="{
                width: (values[index] / max) * 100 + '%',
                background: colors[index % colors.length],
              }"
            />
          </div>
          <strong>{{ item.value }} {{ unit }}</strong>
        </div>
      </div>
      <div v-else-if="type === 'line'" class="report-line">
        <svg
          viewBox="0 0 800 310"
          role="img"
          :aria-label="metricLabel + '趋势'"
        >
          <line x1="50" y1="270" x2="760" y2="270" stroke="#b8c5d5" />
          <line x1="50" y1="50" x2="50" y2="270" stroke="#b8c5d5" />
          <text x="50" y="30">{{ max }} {{ unit }}</text>
          <polyline
            :points="points"
            fill="none"
            stroke="var(--ui-accent)"
            stroke-width="3"
          />
          <circle
            v-for="(item, index) in items"
            :key="item.identity + item.label"
            :cx="50 + (index * 700) / Math.max(1, items.length - 1)"
            :cy="270 - (values[index] / max) * 220"
            r="4"
            fill="var(--ui-accent)"
          >
            <title>{{ name(item.label) }}：{{ item.value }} {{ unit }}</title>
          </circle>
          <text x="50" y="300">{{ name(items[0].label) }}</text>
          <text x="750" y="300" text-anchor="end">
            {{ name(items[items.length - 1].label) }}
          </text>
        </svg>
      </div>
      <div v-else-if="type === 'donut'" class="report-donut">
        <svg
          viewBox="0 0 240 240"
          role="img"
          :aria-label="metricLabel + '构成'"
        >
          <circle
            cx="120"
            cy="120"
            r="85"
            fill="none"
            stroke="#edf1f7"
            stroke-width="32"
          />
          <circle
            v-for="part in slices"
            :key="part.identity + part.label"
            cx="120"
            cy="120"
            r="85"
            fill="none"
            :stroke="part.color"
            stroke-width="32"
            pathLength="100"
            :stroke-dasharray="`${part.size} ${100 - part.size}`"
            :stroke-dashoffset="-part.offset"
            transform="rotate(-90 120 120)"
          >
            <title>{{ name(part.label) }}：{{ part.value }} {{ unit }}</title>
          </circle>
          <text x="120" y="120" text-anchor="middle">{{ metricLabel }}</text>
          <text x="120" y="143" text-anchor="middle">
            {{ total ? "各分组占比" : "合计为零" }}
          </text>
        </svg>
        <ul>
          <li v-for="part in slices" :key="part.identity + part.label">
            <i :style="{ background: part.color }" />{{ name(part.label) }}
            <strong>{{ part.value }} {{ unit }}</strong>
          </li>
        </ul>
      </div>
      <details :open="type === 'table'" class="chart-data">
        <summary>查看图表数据（精确数值）</summary>
        <table>
          <thead>
            <tr>
              <th>分组</th>
              <th>{{ metricLabel }} {{ unit }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.identity + item.label">
              <td>{{ name(item.label) }}</td>
              <td>{{ item.value }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </template>
  </div>
</template>
<style scoped>
.report-chart {
  min-width: 0;
  padding: 24px;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: white;
}
.report-bars {
  display: grid;
  gap: 12px;
}
.report-bar-row {
  display: grid;
  grid-template-columns: minmax(90px, 180px) minmax(60px, 1fr) minmax(
      110px,
      180px
    );
  gap: 14px;
  align-items: center;
}
.report-bar-row > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.report-bar-row strong {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 13px;
}
.report-bar-track {
  background: var(--ui-soft);
  height: 24px;
  border-radius: 4px;
}
.report-bar-track i {
  display: block;
  height: 100%;
  border-radius: 4px;
  min-width: 0;
}
.report-line {
  max-width: 1100px;
  margin: auto;
}
.report-line svg {
  width: 100%;
  height: auto;
}
.report-line text,
.report-donut text {
  fill: var(--ui-text);
  font-size: 13px;
}
.report-donut {
  display: flex;
  gap: 30px;
  align-items: center;
  flex-wrap: wrap;
}
.report-donut svg {
  width: 280px;
  max-width: 100%;
}
.report-donut ul {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 9px;
}
.report-donut li {
  display: flex;
  gap: 10px;
  align-items: center;
}
.report-donut li i {
  width: 10px;
  height: 10px;
  flex: none;
  border-radius: 2px;
}
.chart-data {
  margin-top: 18px;
}
.chart-data summary {
  cursor: pointer;
  color: var(--ui-accent);
}
.chart-data table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 12px;
}
.chart-data th,
.chart-data td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--ui-border);
  text-align: left;
}
.chart-empty {
  padding: 30px;
  text-align: center;
  color: var(--ui-muted);
}
@media (max-width: 600px) {
  .report-chart {
    padding: 12px;
  }
  .report-bar-row {
    grid-template-columns: 80px minmax(40px, 1fr) 100px;
    gap: 6px;
  }
}
</style>
