<script setup lang="ts">
import { ref, watch } from "vue";
import { activeEntity } from "./entityScope";
import { createEditor, goPage } from "./workspace";
import {
  availableShortcuts,
  selectedShortcuts,
  defaultShortcuts,
  saveShortcuts,
  type WorkbenchShortcut,
} from "./workbenchShortcuts";
const opened = ref(false),
  selection = ref<string[]>([]),
  dragging = ref(""),
  message = ref(""),
  launching = ref(false);
watch(opened, (value) => {
  if (value) {
    selection.value = selectedShortcuts.value.map((s) => s.id);
    message.value = "";
  }
});
function choose(id: string, checked: boolean) {
  if (checked) {
    if (selection.value.length >= 9) {
      message.value = "最多显示9个常用操作，请先移除一个。";
      return;
    }
    selection.value.push(id);
  } else selection.value = selection.value.filter((s) => s !== id);
  message.value = "";
}
function move(index: number, step: number) {
  const next = index + step;
  if (next < 0 || next >= selection.value.length) return;
  const list = [...selection.value];
  [list[index], list[next]] = [list[next]!, list[index]!];
  selection.value = list;
}
function drop(id: string) {
  const from = selection.value.indexOf(dragging.value),
    to = selection.value.indexOf(id);
  if (from < 0 || to < 0 || from === to) return;
  const list = [...selection.value];
  list.splice(from, 1);
  list.splice(to, 0, dragging.value);
  selection.value = list;
  dragging.value = "";
}
function apply() {
  saveShortcuts(selection.value);
  opened.value = false;
}
async function launch(item: WorkbenchShortcut) {
  if (launching.value) return;
  launching.value = true;
  try {
    if (item.channel) await createEditor(item.channel, activeEntity.value);
    else goPage(item.page);
  } finally {
    launching.value = false;
  }
}
const name = (id: string) =>
  availableShortcuts.value.find((i) => i.id === id)?.name || "";
</script>
<template>
  <section class="wb-panel" aria-label="常用操作">
    <div class="wb-panel-heading">
      <h3>常用操作</h3>
      <button class="wb-link" @click="opened = true">调整入口</button>
    </div>
    <div class="wb-shortcuts">
      <button
        v-for="s in selectedShortcuts"
        :key="s.id"
        :disabled="launching"
        @click="launch(s)"
      >
        <span class="wb-icon">{{ s.icon }}</span
        >{{ s.name }}
      </button>
    </div>
    <p v-if="!selectedShortcuts.length" class="wb-note">
      可通过“调整入口”选择当前岗位常用的功能。
    </p>
    <el-drawer
      v-model="opened"
      title="调整常用操作"
      class="wb-drawer"
      append-to-body
      ><section class="workbench shortcut-settings">
        <p class="wb-note">
          最多9个，当前浏览器按企业、账号与经营主体记住选择；只显示有权限的入口。可拖动排序，也可使用上移、下移按钮。
        </p>
        <div class="shortcut-options">
          <label v-for="s in availableShortcuts" :key="s.id"
            ><input
              type="checkbox"
              :checked="selection.includes(s.id)"
              :disabled="!selection.includes(s.id) && selection.length >= 9"
              @change="
                choose(s.id, ($event.target as HTMLInputElement).checked)
              "
            />{{ s.name }}</label
          >
        </div>
        <p class="wb-note" role="status">
          {{ message || `已选择 ${selection.length} / 9 个` }}
        </p>
        <h3 style="margin: 16px 0 8px">显示顺序</h3>
        <ol class="shortcut-order">
          <li
            v-for="(id, index) in selection"
            :key="id"
            :data-shortcut="id"
            draggable="true"
            @dragstart="dragging = id"
            @dragend="dragging = ''"
            @dragover.prevent
            @drop.prevent="drop(id)"
          >
            <span aria-hidden="true">⠿</span><strong>{{ name(id) }}</strong
            ><button
              class="wb-button"
              :aria-label="'上移' + name(id)"
              :disabled="index === 0"
              @click="move(index, -1)"
            >
              ↑</button
            ><button
              class="wb-button"
              :aria-label="'下移' + name(id)"
              :disabled="index === selection.length - 1"
              @click="move(index, 1)"
            >
              ↓
            </button>
          </li>
        </ol>
        <p v-if="!selection.length" class="wb-note">
          尚未选择入口，保存后将显示空的常用操作区。
        </p>
        <div class="wb-pagination">
          <button
            class="wb-link"
            @click="selection = defaultShortcuts.map((s) => s.id)"
          >
            恢复默认入口
          </button>
          <div>
            <button class="wb-button" @click="opened = false">取消调整</button
            ><button class="wb-button primary" @click="apply">保存入口</button>
          </div>
        </div>
      </section></el-drawer
    >
  </section>
</template>
<style scoped>
.shortcut-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0;
}
.shortcut-options label {
  display: flex;
  gap: 7px;
  align-items: center;
  font-size: 13px;
}
.shortcut-order {
  list-style: none;
  margin: 0;
  padding: 0;
}
.shortcut-order li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px;
  border-bottom: 1px solid var(--ui-border);
  cursor: grab;
}
.shortcut-order strong {
  flex: 1;
  min-width: 0;
  font-weight: 500;
}
.shortcut-order .wb-button {
  min-height: 28px;
  padding: 2px 8px;
}
@media (max-width: 600px) {
  .shortcut-options {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
