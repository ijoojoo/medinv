<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { can, useCommand } from "./business";
import { goPage } from "./workspace";
import type { WorkQueueItem } from "./workQueue";
const emit = defineEmits<{
  failure: [error: unknown];
  open: [item: WorkQueueItem];
}>();
type Message = {
  message_id: string;
  title: string;
  due: string;
  page: string;
  target_id: string;
  read: boolean;
  work_item?: WorkQueueItem;
};
const source = ref("work"),
  offset = ref(0),
  loading = ref(false),
  failed = ref(false),
  data = ref<{ items: Message[]; has_more: boolean; disabled: boolean }>({
    items: [],
    has_more: false,
    disabled: false,
  });
const { busy, execute } = useCommand((e) => emit("failure", e));
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (reset) offset.value = 0;
  const token = ++epoch;
  loading.value = true;
  failed.value = false;
  try {
    const result = await request<typeof data.value>(
      `/api/v1/messages?entity_id=${activeEntity.value}&source=${source.value}&offset=${offset.value}`,
    );
    if (token === epoch) data.value = result;
  } catch (e) {
    if (token === epoch) {
      failed.value = true;
      emit("failure", e);
    }
  } finally {
    if (token === epoch) loading.value = false;
  }
}
async function mark(row: Message) {
  await execute(
    "/api/v1/messages/read",
    {
      entity_id: activeEntity.value,
      source: source.value,
      message_id: row.message_id,
    },
    () => {
      row.read = true;
    },
  );
}
function open(row: Message) {
  if (row.work_item) emit("open", row.work_item);
  else goPage(row.page);
}
onMounted(() => load());
</script>
<template>
  <section
    class="panel messages-page"
    aria-label="消息和提醒"
    v-loading="loading"
  >
    <header class="master-toolbar">
      <div>
        <h2>消息和提醒</h2>
        <small>业务待办与到期事项实时汇总。已读状态仅属于当前账号。</small>
      </div>
      <el-button @click="load()">刷新提醒</el-button>
    </header>
    <nav class="master-tabs" aria-label="提醒分类">
      <button
        :class="{ active: source === 'work' }"
        @click="
          source = 'work';
          load(true);
        "
      >
        业务待办</button
      ><button
        v-if="can('catalog.read')"
        :class="{ active: source === 'certificate' }"
        @click="
          source = 'certificate';
          load(true);
        "
      >
        资质到期</button
      ><button
        v-if="can('quality.read')"
        :class="{ active: source === 'maintenance' }"
        @click="
          source = 'maintenance';
          load(true);
        "
      >
        养护提醒
      </button>
    </nav>
    <p v-if="failed" role="alert">读取失败，请刷新重试。</p>
    <p v-else-if="data.disabled" class="master-empty-tab">
      当前主体已关闭站内提醒，可在企业设置中开启。
    </p>
    <div v-else>
      <article
        v-for="row in data.items"
        :key="row.message_id"
        class="message-row"
        :class="{ unread: !row.read }"
      >
        <span class="message-dot">{{ row.read ? "○" : "●" }}</span>
        <div>
          <strong>{{ row.title }}</strong>
          <p>
            {{ source === "work" ? "业务发生时间" : "到期日期" }}：{{
              row.due?.replace("T", " ").slice(0, 19)
            }}
            <span>· {{ row.read ? "已读" : "未读" }}</span>
          </p>
        </div>
        <el-button v-if="!row.read" :disabled="busy" @click="mark(row)"
          >标记已读</el-button
        ><el-button type="primary" plain @click="open(row)">{{
          source === "work" ? "进入原单" : "查看资料"
        }}</el-button>
      </article>
      <p v-if="!data.items.length" class="master-empty-tab">
        当前分类没有提醒事项
      </p>
      <div class="master-pagination">
        <el-button
          :disabled="!offset || loading"
          @click="
            offset -= 50;
            load();
          "
          >上一页</el-button
        ><span>{{ Math.floor(offset / 50) + 1 }} 页</span
        ><el-button
          :disabled="!data.has_more || loading"
          @click="
            offset += 50;
            load();
          "
          >下一页</el-button
        >
      </div>
    </div>
  </section>
</template>
<style scoped>
.messages-page {
  max-width: 1200px;
  margin: auto;
}
.master-toolbar small {
  display: block;
  margin-top: 8px;
  color: var(--ui-muted);
}
.message-row {
  display: flex;
  gap: 14px;
  align-items: center;
  padding: 18px 12px;
  border-bottom: 1px solid var(--ui-border);
}
.message-row > div {
  flex: 1;
  min-width: 0;
}
.message-row strong {
  font-size: 14px;
  overflow-wrap: anywhere;
}
.message-row p {
  font-size: 12px;
  color: var(--ui-muted);
  margin: 6px 0;
}
.message-dot {
  color: var(--ui-muted);
}
.unread .message-dot {
  color: var(--ui-accent);
}
.unread {
  background: var(--ui-tint);
}
@media (max-width: 650px) {
  .message-row {
    flex-wrap: wrap;
  }
  .message-row > div {
    min-width: 80%;
  }
}
</style>
