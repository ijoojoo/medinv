<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { cents, money, amount } from "./workbench";
const props = defineProps<{
  series: { day: string; sales: string | null; cash: string | null }[];
  metric: "sales" | "cash";
}>();
const root = ref<HTMLElement>(),
  width = ref(640),
  index = ref(0),
  hover = ref<number>();
let observer: ResizeObserver | undefined;
onMounted(() => {
  observer = new ResizeObserver((entries) => {
    width.value = Math.max(210, entries[0]!.contentRect.width);
  });
  if (root.value) observer.observe(root.value);
});
onUnmounted(() => observer?.disconnect());
watch(
  () => [props.series, props.metric],
  () => {
    index.value = Math.max(0, props.series.length - 1);
    hover.value = undefined;
  },
  { immediate: true },
);
const label = computed(() => (props.metric === "sales" ? "销售额" : "回款"));
const numbers = computed(() =>
  props.series.map((r) =>
    r[props.metric] == null ? null : Number(cents(r[props.metric]!)) / 100,
  ),
);
const incomplete = computed(() => numbers.value.some((v) => v === null));
const bounds = computed(() => {
  const known = numbers.value.filter((v): v is number => v !== null);
  const low = Math.min(0, ...known),
    high = Math.max(1, ...known);
  return { low, high: high + (high - low) * 0.08 };
});
const x = (i: number) =>
  48 + (i * (width.value - 62)) / Math.max(1, props.series.length - 1);
const y = (v: number) =>
  190 - ((v - bounds.value.low) / (bounds.value.high - bounds.value.low)) * 170;
const segments = computed(() => {
  const result: { start: number; end: number; path: string }[] = [];
  let current: (typeof result)[number] | undefined;
  numbers.value.forEach((v, i) => {
    if (v === null) {
      current = undefined;
      return;
    }
    if (!current) {
      current = { start: i, end: i, path: `M${x(i)},${y(v)}` };
      result.push(current);
    } else {
      current.end = i;
      current.path += ` L${x(i)},${y(v)}`;
    }
  });
  return result;
});
const line = computed(() => segments.value.map((s) => s.path).join(" "));
const area = computed(() =>
  segments.value
    .map((s) => `${s.path} L${x(s.end)},${y(0)} L${x(s.start)},${y(0)} Z`)
    .join(" "),
);
const marks = computed(() =>
  [
    ...new Set([
      0,
      Math.floor((props.series.length - 1) / 3),
      Math.floor(((props.series.length - 1) * 2) / 3),
      props.series.length - 1,
    ]),
  ].filter((i) => i >= 0),
);
const current = computed(() => props.series[hover.value ?? index.value]);
const average = computed(() => {
  if (incomplete.value) return "—";
  const n = BigInt(props.series.length || 1),
    sum = props.series.reduce((s, r) => s + cents(r[props.metric]!), 0n);
  return money(amount(sum >= 0n ? (sum + n / 2n) / n : -((-sum + n / 2n) / n)));
});
const peak = computed(() =>
  props.series.reduce<string | undefined>(
    (max, r) =>
      r[props.metric] !== null &&
      (max === undefined || cents(r[props.metric]!) > cents(max))
        ? r[props.metric]!
        : max,
    undefined,
  ),
);
function point(e: PointerEvent) {
  if (!root.value || !props.series.length) return;
  hover.value = Math.max(
    0,
    Math.min(
      props.series.length - 1,
      Math.round(
        ((e.clientX - root.value.getBoundingClientRect().left - 48) /
          (width.value - 62)) *
          (props.series.length - 1),
      ),
    ),
  );
}
function step(direction: number) {
  hover.value = undefined;
  index.value = Math.max(
    0,
    Math.min(props.series.length - 1, index.value + direction),
  );
}
</script>
<template>
  <div class="wb-trend-summary">
    <span
      >日均 <b>{{ average }}</b> 元</span
    ><span
      >{{ incomplete ? "已知单日最高" : "单日最高" }}
      <b>{{ money(peak) }}</b> 元</span
    >
  </div>
  <p v-if="incomplete" class="wb-note" role="status">
    部分历史记录金额不完整：对应日期留空，不计算完整期间日均。
  </p>
  <div
    ref="root"
    class="wb-chart"
    @pointermove="point"
    @pointerleave="hover = undefined"
    @pointerdown="point"
  >
    <svg
      :viewBox="`0 0 ${width} 220`"
      role="img"
      :aria-label="`${label}逐日趋势，人民币元；下方可逐日查看准确金额`"
    >
      <g v-for="n in [0, 1, 2, 3, 4]" :key="n">
        <line
          x1="48"
          :x2="width - 14"
          :y1="20 + n * 42.5"
          :y2="20 + n * 42.5"
          class="wb-gridline"
        />
        <text x="42" :y="24 + n * 42.5" text-anchor="end">
          {{
            new Intl.NumberFormat("zh-CN", {
              notation: "compact",
              maximumFractionDigits: 1,
            }).format(bounds.high - ((bounds.high - bounds.low) * n) / 4)
          }}
        </text>
      </g>
      <path :d="area" fill="var(--ui-accent)" opacity=".06" />
      <path
        :d="line"
        fill="none"
        stroke="var(--ui-accent)"
        stroke-width="2.3"
        stroke-linejoin="round"
      />
      <text
        v-for="i in marks"
        :key="i"
        :x="x(i)"
        y="213"
        :text-anchor="
          i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle'
        "
      >
        {{ series[i]?.day.slice(5).replace("-", "/") }}
      </text>
      <template v-if="current">
        <line
          v-if="hover !== undefined"
          :x1="x(hover)"
          :x2="x(hover)"
          y1="20"
          y2="190"
          class="wb-cursor"
        />
        <circle
          v-if="numbers[hover ?? index] != null"
          :cx="x(hover ?? index)"
          :cy="y(numbers[hover ?? index] || 0)"
          r="3.5"
          fill="var(--ui-accent)"
        />
      </template>
    </svg>
  </div>
  <div class="wb-chart-reading">
    <span aria-live="polite"
      >{{ current?.day }} · {{ label }}
      <b>{{ money(current?.[metric]) }}</b> 元</span
    >
    <div>
      <button aria-label="查看前一日" :disabled="index <= 0" @click="step(-1)">
        ‹</button
      ><button
        aria-label="查看后一日"
        :disabled="index >= series.length - 1"
        @click="step(1)"
      >
        ›
      </button>
    </div>
  </div>
</template>
