<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { can, useWorkbenchData } from "./workbench";
import { editors, openDraft } from "./workspace";
import { localTime } from "./sales";
import {
  canReadDrafts,
  draftName,
  draftStatus,
  pendingDrafts,
  type DraftList,
  type DraftSummary,
} from "./draftSummaries";
import WorkbenchStatus from "./WorkbenchStatus.vue";
const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    entity: string;
    channel?: "purchase" | "sale" | "all";
    active?: boolean;
  }>(),
  { channel: "all", active: true },
);
const emit = defineEmits<{ "update:modelValue": [boolean] }>();
const opened = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit("update:modelValue", v),
});
const channel = ref(props.channel),
  search = ref(""),
  query = ref(""),
  offset = ref(0),
  limit = 20;
watch(
  () => props.channel,
  (value) => {
    channel.value = value;
    offset.value = 0;
  },
);
watch(
  () => props.entity,
  () => {
    search.value = "";
    query.value = "";
    offset.value = 0;
  },
);
const { data, loading, error, load } = useWorkbenchData<DraftList>(
  () =>
    "/api/v1/document-drafts?" +
    new URLSearchParams({
      entity_id: props.entity,
      channel: channel.value,
      search: query.value,
      offset: String(offset.value),
      limit: String(limit),
    }),
  () => props.active && opened.value && canReadDrafts.value,
);
const pending = computed(() =>
  pendingDrafts.value.filter(
    (s) =>
      s.record.entity_id === props.entity &&
      (channel.value === "all" || s.record.channel === channel.value),
  ),
);
function find() {
  if (query.value === search.value.trim() && !offset.value) void load();
  else {
    query.value = search.value.trim();
    offset.value = 0;
  }
}
function reset() {
  search.value = "";
  query.value = "";
  offset.value = 0;
}
function resume(row: DraftSummary) {
  opened.value = false;
  void openDraft(row.id, row.channel);
}
</script>
<template>
  <el-drawer
    v-model="opened"
    title="我的编辑草稿"
    class="wb-drawer"
    append-to-body
  >
    <section class="workbench draft-browser" aria-label="草稿检索">
      <p class="wb-note">
        仅当前经营主体下、您本人有权编辑的草稿。继续录入会保留当前窗口的未保存修改；草稿尚未进入审核。
      </p>
      <div v-if="pending.length" class="draft-pending">
        <h3>当前窗口待保存 · {{ pending.length }}份</h3>
        <p class="wb-note">
          以下草稿仍有待保存内容，可打开后继续录入或重试保存。下方查询仅匹配已保存的内容。
        </p>
        <button
          v-for="s in pending"
          :key="s.record.id"
          class="wb-draft"
          @click="resume(s.record)"
        >
          <strong>{{ draftName(s.record) }}</strong
          ><small>{{ draftStatus(s) }}</small>
        </button>
      </div>
      <form class="wb-search" @submit.prevent="find" style="margin-top: 16px">
        <select v-model="channel" aria-label="草稿类型" @change="offset = 0">
          <option value="all">全部类型</option>
          <option v-if="can('purchase.write')" value="purchase">
            采购草稿
          </option>
          <option v-if="can('sales.write')" value="sale">
            销售草稿
          </option></select
        ><input
          v-model="search"
          type="search"
          aria-label="查找草稿"
          placeholder="备注、往来单位、仓库或改单原单号"
          maxlength="100"
        /><button class="wb-button" type="submit">查询草稿</button
        ><button class="wb-link" type="button" @click="reset">清空检索</button>
      </form>
      <WorkbenchStatus
        :loading="loading"
        :error="error"
        :has-data="!!data"
        @retry="load"
      />
      <template v-if="data"
        ><p class="wb-note">
          已存草稿共 {{ data.total }} 份 ·
          按最近保存时间排序；匹配已保存的资料，空行不计入商品行数。
        </p>
        <div class="wb-scroll">
          <table class="wb-table">
            <thead>
              <tr>
                <th>草稿 / 原单</th>
                <th>往来单位 / 仓库</th>
                <th>商品行 / 最近保存</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in data.items" :key="r.id">
                <td>
                  <strong>{{ draftName(r) }}</strong
                  ><small
                    >{{ r.channel === "purchase" ? "采购" : "销售" }} ·
                    {{ r.source_order_id ? "修改已有单据" : "新建单据" }}</small
                  ><small v-if="r.source_number"
                    >原单 {{ r.source_number }}</small
                  >
                </td>
                <td>
                  {{ r.party_name || "尚未选择往来单位"
                  }}<small>{{ r.warehouse_name || "尚未选择仓库" }}</small>
                </td>
                <td>
                  {{ r.line_count }} 行<small>{{
                    localTime(r.updated_at)
                  }}</small
                  ><small v-if="editors[r.id]">{{
                    draftStatus(editors[r.id])
                  }}</small>
                </td>
                <td>
                  <button class="wb-link" @click="resume(r)">继续录入 ↗</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!data.items.length" class="wb-empty">
          {{
            query
              ? "没有匹配的已存草稿，可更换关键词。"
              : "当前类型暂无已存草稿。"
          }}
        </p>
        <div class="wb-pagination">
          <span>第 {{ offset / limit + 1 }} 页 · 每页{{ limit }}份</span>
          <div>
            <button
              class="wb-button"
              :disabled="!offset || loading"
              @click="offset = Math.max(0, offset - limit)"
            >
              上一页草稿</button
            ><button
              class="wb-button"
              :disabled="!data.has_more || loading"
              @click="offset += limit"
            >
              下一页草稿
            </button>
          </div>
        </div></template
      >
    </section>
  </el-drawer>
</template>
<style scoped>
.draft-pending {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--ui-border);
  background: var(--ui-tint);
  border-radius: 8px;
}
.draft-browser td {
  overflow-wrap: anywhere;
  max-width: 310px;
}
.draft-browser td strong {
  font-weight: 500;
}
.draft-browser .wb-search {
  gap: 8px;
}
</style>
