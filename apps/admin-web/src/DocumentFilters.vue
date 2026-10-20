<script setup lang="ts">
import DateRange from "./DateRange.vue";
import PartyLookup from "./PartyLookup.vue";
import { datePresets, presetRange, type DatePreset } from "./datePresets";
import { computed, nextTick, ref, useId, watch } from "vue";
import { emptyDocumentSearch, type DocumentSearch } from "./documentSearch";
const props = defineProps<{
  applied: DocumentSearch;
  states: Record<string, string>;
  placeholder: string;
  disabled: boolean;
  entity?: string;
  channel?: "purchase" | "sale";
  warehouses?: { id: string; name: string }[];
  advancedState?: boolean;
  dateLabel?: string;
  stateLabel?: string;
  kinds?: Record<string, string>;
}>();
const emit = defineEmits<{ apply: [filters: DocumentSearch] }>();
const draft = ref({ ...props.applied });
const more = ref(
    !!props.applied.warehouse_id ||
      !!props.applied.party_id ||
      !!props.applied.kind ||
      (!!props.advancedState && !!props.applied.state),
  ),
  id = useId();
const preset = ref<DatePreset | "custom">(
  props.applied.date_from || props.applied.date_to ? "custom" : "all",
);
const dateResetKey = ref(0);
const resetKey = ref(0);
const hasMore = computed(
  () => props.channel || props.advancedState || props.kinds,
);
const extraCount = computed(
  () =>
    Number(!!draft.value.warehouse_id) +
    Number(!!draft.value.party_id) +
    Number(!!draft.value.kind) +
    Number(!!props.advancedState && !!draft.value.state),
);
function range(key: DatePreset) {
  Object.assign(draft.value, presetRange(key));
  preset.value = key;
  dateResetKey.value++;
}
function selectRange(event: Event) {
  const value = (event.target as HTMLSelectElement).value as
    DatePreset | "custom";
  if (value !== "custom") range(value);
}
watch(
  () => [draft.value.date_from, draft.value.date_to],
  () => {
    if (preset.value !== "custom") {
      const r = presetRange(preset.value);
      if (
        r.date_from !== draft.value.date_from ||
        r.date_to !== draft.value.date_to
      )
        preset.value =
          draft.value.date_from || draft.value.date_to ? "custom" : "all";
    }
  },
);
function apply() {
  emit("apply", { ...draft.value, search: draft.value.search.trim() });
}
function clear() {
  resetKey.value++;
  dateResetKey.value++;
  draft.value = emptyDocumentSearch();
  preset.value = "all";
  apply();
}
function revealInvalid(event: Event) {
  const input = event.target as HTMLInputElement;
  if (more.value || !input.closest(".document-extra-filters")) return;
  event.preventDefault();
  more.value = true;
  void nextTick(() => {
    input.focus();
    input.reportValidity();
  });
}
</script>
<template>
  <form
    class="business-form document-filters"
    aria-label="单据查询"
    data-date-query
    @submit.prevent="apply"
    @invalid.capture="revealInvalid"
  >
    <label class="document-keyword"
      >查找单据<input
        v-model="draft.search"
        :placeholder="placeholder"
        maxlength="100"
        type="search"
    /></label>
    <label v-if="!advancedState"
      >{{ stateLabel || "单据状态"
      }}<select v-model="draft.state">
        <option value="">全部状态</option>
        <option v-for="(label, state) in states" :key="state" :value="state">
          {{ label }}
        </option>
      </select></label
    >
    <label
      class="date-preset"
      title="按北京时间填入范围，点击查询执行；也可手工调整起止日期。"
      >快捷日期<select
        v-model="preset"
        aria-label="快捷日期范围"
        @change="selectRange"
      >
        <option value="custom">自定义日期</option>
        <option v-for="[key, name] in datePresets" :key="key" :value="key">
          {{ name }}
        </option>
      </select></label
    >
    <DateRange
      :key="dateResetKey"
      v-model:start="draft.date_from"
      v-model:end="draft.date_to"
      :label="dateLabel || '创建日期'"
      max="9999-12-30"
      query
    />
    <div class="document-filter-actions">
      <el-button native-type="submit" type="primary" :disabled="disabled"
        >查询单据</el-button
      >
      <el-button :disabled="disabled" @click="clear">清除筛选</el-button>
      <el-button
        v-if="hasMore"
        :aria-expanded="more"
        :aria-controls="id"
        @click="more = !more"
        >{{ more ? "收起条件" : "更多条件"
        }}{{ extraCount ? `（${extraCount}）` : "" }}</el-button
      >
    </div>
    <div v-if="hasMore" v-show="more" :id="id" class="document-extra-filters">
      <label v-if="advancedState"
        >{{ stateLabel || "单据状态"
        }}<select v-model="draft.state">
          <option value="">全部{{ stateLabel || "状态" }}</option>
          <option v-for="(label, state) in states" :key="state" :value="state">
            {{ label }}
          </option>
        </select></label
      >
      <label v-if="kinds"
        >处置方式<select v-model="draft.kind">
          <option value="">全部方式</option>
          <option v-for="(label, kind) in kinds" :key="kind" :value="kind">
            {{ label }}
          </option>
        </select></label
      >
      <label v-if="channel"
        >{{ channel === "purchase" ? "供应商" : "客户"
        }}<PartyLookup
          :key="resetKey"
          v-model="draft.party_id"
          :entity="entity || ''"
          :channel="channel"
          :label="channel === 'purchase' ? '筛选供应商' : '筛选客户'"
          :qualified-only="false"
      /></label>
      <label v-if="channel"
        >仓库<select v-model="draft.warehouse_id" aria-label="仓库">
          <option value="">全部仓库</option>
          <option v-for="w in warehouses || []" :key="w.id" :value="w.id">
            {{ w.name }}
          </option>
        </select></label
      >
      <span class="document-filter-note"
        >收起后条件仍保留；清除筛选可重置全部条件。日期按北京时间。</span
      >
    </div>
  </form>
</template>
<style scoped>
.document-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 12px;
  margin: 16px 0;
}
.document-filters label {
  flex: 1 1 140px;
}
.document-filters .document-keyword {
  flex: 2 1 260px;
}
.document-filter-actions {
  display: flex;
  align-items: center;
  min-height: 38px;
}
.document-filter-note {
  flex-basis: 100%;
  color: var(--ui-muted);
}
.document-filters .date-preset {
  flex: 0 1 124px;
  min-width: 114px;
}
.document-extra-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  flex-basis: 100%;
  padding: 14px;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: var(--ui-tint);
}
.document-extra-filters label {
  flex: 1 1 240px;
  max-width: 520px;
}
</style>
