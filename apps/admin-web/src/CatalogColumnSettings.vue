<script setup lang="ts">
import { computed, ref } from "vue";
import {
  normalizeListColumns,
  type ListColumn,
  type ListColumnDefinition,
} from "./catalogColumns";
const props = defineProps<{
  title: string;
  columns: ListColumn[];
  definitions: ListColumnDefinition[];
  save: (columns: ListColumn[]) => string | null;
}>();
const emit = defineEmits<{ close: [] }>();
const draft = ref(props.columns.map((c) => ({ ...c })));
const search = ref(""),
  error = ref(""),
  dragged = ref("");
const matches = computed(() =>
  draft.value.filter((c) =>
    c.label
      .toLocaleLowerCase()
      .includes(search.value.trim().toLocaleLowerCase()),
  ),
);
function move(key: string, delta: number) {
  const index = draft.value.findIndex((c) => c.key === key),
    next = index + delta;
  if (index < 0 || next < 0 || next >= draft.value.length) return;
  draft.value.splice(next, 0, draft.value.splice(index, 1)[0]!);
}
function drop(key: string) {
  const from = draft.value.findIndex((c) => c.key === dragged.value),
    to = draft.value.findIndex((c) => c.key === key);
  if (from >= 0 && to >= 0 && from !== to)
    draft.value.splice(to, 0, draft.value.splice(from, 1)[0]!);
  dragged.value = "";
}
function apply() {
  const invalid = draft.value.find(
    (c) => !Number.isInteger(c.width) || c.width < 80 || c.width > 640,
  );
  if (invalid) {
    error.value = `${invalid.label}：列宽须为 80–640 的整数。`;
    search.value = "";
    return;
  }
  error.value = props.save(draft.value) || "";
  if (!error.value) emit("close");
}
</script>
<template>
  <el-dialog
    :model-value="true"
    append-to-body
    :title="title"
    width="850px"
    top="4vh"
    class="business-dialog catalog-column-dialog"
    :close-on-click-modal="false"
    @close="emit('close')"
  >
    <p class="column-note">
      当前账号 · 当前企业 ·
      本浏览器保存。拖动左侧手柄或用前移、后移调整顺序；名称、编码、状态及操作入口保持可见。
    </p>
    <div class="column-tools">
      <input
        v-model="search"
        aria-label="查找列表字段"
        placeholder="查找字段"
        type="search"
      />
      <span
        >已显示 {{ draft.filter((c) => c.visible).length }} /
        {{ draft.length }} 列</span
      >
    </div>
    <p v-if="error" role="alert" class="column-error">{{ error }}</p>
    <div class="column-scroll">
      <table>
        <thead>
          <tr>
            <th>顺序 / 字段</th>
            <th>显示</th>
            <th>列宽（px）</th>
            <th>文本换行</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="column in matches"
            :key="column.key"
            :data-list-column="column.key"
            @dragover.prevent
            @drop.prevent="drop(column.key)"
          >
            <td>
              <div class="column-name">
                <span
                  class="column-drag"
                  draggable="true"
                  :aria-label="column.label + ' 拖动排序'"
                  @dragstart="
                    dragged = column.key;
                    $event.dataTransfer?.setData('text/plain', column.key);
                  "
                  @dragend="dragged = ''"
                  >⠿</span
                >
                <strong>{{ column.label }}</strong>
              </div>
              <div class="column-move">
                <button
                  type="button"
                  :aria-label="column.label + ' 前移'"
                  :disabled="draft[0]?.key === column.key"
                  @click="move(column.key, -1)"
                >
                  ↑ 前移
                </button>
                <button
                  type="button"
                  :aria-label="column.label + ' 后移'"
                  :disabled="draft.at(-1)?.key === column.key"
                  @click="move(column.key, 1)"
                >
                  ↓ 后移
                </button>
              </div>
            </td>
            <td>
              <input
                v-model="column.visible"
                type="checkbox"
                :aria-label="column.label + ' 显示'"
                :disabled="column.required"
              />
            </td>
            <td>
              <input
                v-model.number="column.width"
                type="number"
                :aria-label="column.label + ' 列宽'"
                min="80"
                max="640"
                step="1"
                required
              />
            </td>
            <td>
              <input
                v-model="column.wrap"
                type="checkbox"
                :aria-label="column.label + ' 换行'"
              />
            </td>
          </tr>
          <tr v-if="!matches.length">
            <td colspan="4">没有匹配字段，请换一个关键词。</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="column-note">
      宽度不足时列表可横向滚动；未开启换行的长内容可悬停查看。这里只调整查询列表，不改变建档字段、单据录入或导出内容。
    </p>
    <template #footer>
      <div class="column-footer">
        <el-button
          @click="
            draft = normalizeListColumns(null, definitions);
            search = '';
            error = '';
          "
          >恢复默认列</el-button
        >
        <span></span>
        <el-button @click="emit('close')">取消</el-button>
        <el-button type="primary" @click="apply">保存并应用</el-button>
      </div>
    </template>
  </el-dialog>
</template>
<style scoped>
:global(.catalog-column-dialog) {
  display: flex;
  flex-direction: column;
  max-height: 92dvh;
}
:global(.catalog-column-dialog .el-dialog__body) {
  min-height: 0;
  max-height: none;
  overflow: auto;
}
:global(.catalog-column-dialog .el-dialog__header),
:global(.catalog-column-dialog .el-dialog__footer) {
  flex: none;
}
.column-note {
  color: var(--ui-muted);
  font-size: 13px;
  line-height: 1.7;
  margin: 0 0 12px;
}
.column-tools,
.column-footer {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.column-tools input {
  flex: 1;
  min-width: 130px;
}
.column-tools span {
  font-size: 13px;
  color: var(--ui-muted);
}
.column-footer {
  margin: 0;
}
.column-footer span {
  flex: 1;
}
.column-footer .el-button + .el-button {
  margin: 0;
}
.column-scroll {
  max-height: 51dvh;
  overflow: auto;
  margin-bottom: 12px;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 580px;
  font-size: 13px;
}
th,
td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--ui-border);
  text-align: left;
}
th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--ui-tint);
}
th:first-child {
  width: 48%;
}
.column-name {
  display: flex;
  align-items: center;
  gap: 6px;
}
.column-drag {
  cursor: grab;
  font-size: 20px;
}
.column-move {
  display: flex;
  gap: 6px;
  margin-top: 5px;
}
.column-move button {
  border: 1px solid var(--ui-border);
  border-radius: 4px;
  background: var(--surface, #fff);
  color: var(--ui-accent);
  padding: 3px 7px;
  cursor: pointer;
}
.column-move button:disabled {
  color: var(--ui-muted);
  cursor: default;
  opacity: 0.55;
}
input:not([type="checkbox"]) {
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  font: inherit;
}
input[type="number"] {
  width: 95px;
}
input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--ui-accent);
}
input:focus-visible,
button:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}
.column-error {
  color: var(--el-color-danger);
}
</style>
