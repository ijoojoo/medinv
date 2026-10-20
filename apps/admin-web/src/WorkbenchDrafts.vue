<script setup lang="ts">
import { computed, ref } from "vue";
import { activeEntity } from "./entityScope";
import { openDraft } from "./workspace";
import { localTime } from "./sales";
import { useWorkbenchData } from "./workbench";
import {
  canReadDrafts,
  currentDrafts,
  pendingDrafts,
  needsDraftAttention,
  draftName,
  draftStatus,
  summarizeDraft,
  type DraftList,
  type DraftSummary,
} from "./draftSummaries";
import WorkbenchStatus from "./WorkbenchStatus.vue";
import DraftBrowser from "./DraftBrowser.vue";
const props = defineProps<{ active: boolean }>();
const opened = ref(false);
const { data, loading, error, load } = useWorkbenchData<DraftList>(
  () =>
    `/api/v1/document-drafts?entity_id=${activeEntity.value}&channel=all&limit=4`,
  () => props.active && canReadDrafts.value,
);
const drafts = computed(() => {
  const result = new Map<
    string,
    { record: DraftSummary; status: string; priority: number }
  >();
  for (const record of data.value?.items || [])
    result.set(record.id, { record, status: draftStatus(), priority: 0 });
  for (const state of currentDrafts.value)
    result.set(state.record.id, {
      record: {
        ...result.get(state.record.id)?.record,
        ...summarizeDraft(state),
      },
      status: draftStatus(state),
      priority: needsDraftAttention(state) ? 1 : 0,
    });
  return [...result.values()]
    .sort(
      (a, b) =>
        b.priority - a.priority ||
        b.record.updated_at.localeCompare(a.record.updated_at) ||
        a.record.id.localeCompare(b.record.id),
    )
    .slice(0, 4);
});
defineExpose({ refresh: load });
</script>
<template>
  <section v-if="canReadDrafts" class="wb-panel" aria-label="继续草稿">
    <div class="wb-panel-heading">
      <h3>继续草稿</h3>
      <button class="wb-link" @click="opened = true">全部草稿 ↗</button>
    </div>
    <p class="wb-note">优先待保存，其余按最近保存排序。</p>
    <WorkbenchStatus
      :loading="loading"
      :error="error"
      :has-data="!!drafts.length"
      @retry="load"
    /><button
      v-for="d in drafts"
      :key="d.record.id"
      class="wb-draft"
      @click="openDraft(d.record.id, d.record.channel)"
    >
      <strong>{{ draftName(d.record) }}</strong
      ><small v-if="d.record.party_name || d.record.warehouse_name">{{
        [d.record.party_name, d.record.warehouse_name]
          .filter(Boolean)
          .join(" · ")
      }}</small
      ><small>{{ d.record.line_count ?? 0 }} 行 · {{ d.status }}</small
      ><small v-if="d.record.updated_at">{{
        localTime(d.record.updated_at)
      }}</small>
    </button>
    <p
      v-if="!drafts.length && !loading && !error"
      class="wb-note"
      style="margin-top: 12px"
    >
      暂无可接续草稿。
    </p>
    <button
      v-if="pendingDrafts.length > 4"
      class="wb-link"
      @click="opened = true"
    >
      查看全部 {{ pendingDrafts.length }} 份待保存草稿 ↗</button
    ><DraftBrowser v-model="opened" :entity="activeEntity" :active="active" />
  </section>
</template>
