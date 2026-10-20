<script setup lang="ts">
import { computed, ref, toRef } from "vue";
import {
  defaultEntryColumns,
  columnLabel,
  normalizeEntryColumns,
  type EntryChannel,
  type EntryColumn,
} from "./documentEntry";
import {
  useEntryTemplate,
  saveEntryTemplate,
  reloadEntryTemplate,
} from "./entryTemplates";
const props = defineProps<{ channel: EntryChannel }>();
const emit = defineEmits<{ close: [] }>();
const state = useEntryTemplate(toRef(props, "channel"));
const pendingBody = state.value.pending
  ? JSON.parse(state.value.pending.body)
  : null;
const columns = ref<EntryColumn[]>(
  pendingBody
    ? normalizeEntryColumns(pendingBody.template.columns)
    : state.value.columns.map((c) => ({ ...c })),
);
const version = ref(pendingBody?.expected_version || state.value.version);
const localError = ref("");
const dragKey = ref("");
const names = Object.fromEntries(
  defaultEntryColumns().map((c) => [c.key, c.label]),
);
const sequence = computed(() =>
  columns.value
    .filter((c) => c.visible && c.tab_stop)
    .map(columnLabel)
    .join(" → "),
);
function move(index: number, next: number) {
  if (
    state.value.saving ||
    state.value.pending ||
    next < 0 ||
    next >= columns.value.length
  )
    return;
  columns.value.splice(next, 0, columns.value.splice(index, 1)[0]!);
}
function drop(key: string) {
  const from = columns.value.findIndex((c) => c.key === dragKey.value),
    to = columns.value.findIndex((c) => c.key === key);
  if (from >= 0) move(from, to);
  dragKey.value = "";
}
async function reload() {
  await reloadEntryTemplate(state.value);
  if (!state.value.error) {
    columns.value = state.value.columns.map((c) => ({ ...c }));
    version.value = state.value.version;
  }
}
async function save() {
  localError.value = "";
  for (const c of columns.value) {
    const min = c.width_mode === "weight" ? 1 : c.key === "product" ? 220 : 90;
    const max = c.width_mode === "weight" ? 20 : 600;
    if (
      !c.label.trim() ||
      !Number.isInteger(c.width) ||
      c.width < min ||
      c.width > max
    ) {
      localError.value = `${names[c.key]}：请填写名称，宽度须为 ${min}–${max} 的整数`;
      return;
    }
  }
  if (await saveEntryTemplate(state.value, columns.value, version.value))
    emit("close");
}
</script>
<template>
  <el-dialog
    :model-value="true"
    append-to-body
    title="单据列设置"
    class="business-dialog"
    width="1100px"
    :close-on-click-modal="false"
    :close-on-press-escape="!state.saving"
    :show-close="!state.saving"
    @close="emit('close')"
  >
    <p class="template-note">
      当前账号 · 当前企业 ·
      {{
        channel === "purchase" ? "采购" : "销售"
      }}。保存后新建与改单共用，可跨设备读取。商品、数量、含税单价必须显示；商品和数量必须停留。
    </p>
    <p class="template-sequence">回车 / Tab 顺序：{{ sequence }} → 下一行</p>
    <p class="template-note">
      固定宽度单位为
      px；比例分配剩余空间，窄屏保留最小列宽并横向滚动。格式仅改变显示，进入输入框时仍显示完整原值；税率百分比显示时，编辑仍按小数填写。
    </p>
    <p v-if="state.version === '0'" class="template-note">
      尚未保存个人设置；已兼容本浏览器旧版列顺序和显隐。
    </p>
    <p v-if="localError || state.error" class="entry-error" role="alert">
      {{ localError || state.error }}
    </p>
    <fieldset
      :disabled="state.saving || !!state.pending"
      class="template-fields"
    >
      <div class="template-scroll">
        <table class="template-table">
          <thead>
            <tr>
              <th>顺序 / 系统字段</th>
              <th>显示名称</th>
              <th>显示</th>
              <th>回车停留</th>
              <th>列宽</th>
              <th>显示格式</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(c, i) in columns"
              :key="c.key"
              class="entry-column-setting"
              :data-field="c.key"
              @dragover.prevent
              @drop.prevent="drop(c.key)"
            >
              <td>
                <span
                  class="template-drag"
                  draggable="true"
                  :aria-label="names[c.key] + ' 拖动排序'"
                  @dragstart="
                    dragKey = c.key;
                    $event.dataTransfer?.setData('text/plain', c.key);
                  "
                  @dragend="dragKey = ''"
                  >⠿</span
                ><b>{{ names[c.key] }}</b>
                <div class="template-order">
                  <button
                    type="button"
                    :disabled="i === 0"
                    :aria-label="names[c.key] + ' 前移'"
                    @click="move(i, i - 1)"
                  >
                    ↑ 前移</button
                  ><button
                    type="button"
                    :disabled="i === columns.length - 1"
                    :aria-label="names[c.key] + ' 后移'"
                    @click="move(i, i + 1)"
                  >
                    ↓ 后移
                  </button>
                </div>
              </td>
              <td>
                <input
                  v-model="c.label"
                  :aria-label="names[c.key] + ' 显示名称'"
                  maxlength="30"
                />
              </td>
              <td>
                <input
                  v-model="c.visible"
                  type="checkbox"
                  :aria-label="names[c.key]"
                  :disabled="c.required"
                />
              </td>
              <td>
                <input
                  v-model="c.tab_stop"
                  type="checkbox"
                  :aria-label="names[c.key] + ' 回车停留'"
                  :disabled="!c.editable || ['product', 'qty'].includes(c.key)"
                />
              </td>
              <td>
                <div class="template-width">
                  <select
                    v-model="c.width_mode"
                    :aria-label="names[c.key] + ' 宽度模式'"
                    @change="
                      c.width =
                        c.width_mode === 'weight'
                          ? 1
                          : c.key === 'product'
                            ? 270
                            : 130
                    "
                  >
                    <option value="px">固定</option>
                    <option value="weight">比例</option></select
                  ><input
                    v-model.number="c.width"
                    type="number"
                    :aria-label="names[c.key] + ' 宽度'"
                    :min="
                      c.width_mode === 'weight'
                        ? 1
                        : c.key === 'product'
                          ? 220
                          : 90
                    "
                    :max="c.width_mode === 'weight' ? 20 : 600"
                    step="1"
                  />
                </div>
              </td>
              <td>
                <template v-if="['qty', 'price', 'amount'].includes(c.key)"
                  ><select
                    v-model="c.decimals"
                    :aria-label="names[c.key] + ' 小数位'"
                  >
                    <option :value="-1">原始小数位</option>
                    <option v-for="n in 7" :key="n" :value="n - 1">
                      显示 {{ n - 1 }} 位小数
                    </option></select
                  ><label
                    ><input
                      v-model="c.grouping"
                      type="checkbox"
                      :aria-label="names[c.key] + ' 千分位'"
                    />千分位</label
                  ></template
                >
                <select
                  v-else-if="c.key === 'tax'"
                  v-model="c.tax_display"
                  aria-label="税率显示格式"
                >
                  <option value="decimal">小数（0.13）</option>
                  <option value="percent">百分比（13%）</option>
                </select>
                <label
                  v-else-if="
                    ['product', 'specification', 'manufacturer_name'].includes(
                      c.key,
                    )
                  "
                  ><input
                    v-model="c.wrap"
                    type="checkbox"
                    :aria-label="names[c.key] + ' 自动换行'"
                  />文本自动换行</label
                ><span v-else>单位名称</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </fieldset>
    <template #footer
      ><div class="template-footer">
        <el-button
          :disabled="state.saving || !!state.pending"
          @click="columns = defaultEntryColumns()"
          >恢复默认列</el-button
        ><span></span
        ><el-button :disabled="state.saving || state.loading" @click="reload"
          >重新读取已存设置</el-button
        ><el-button :disabled="state.saving" @click="emit('close')"
          >取消</el-button
        ><el-button
          type="primary"
          :loading="state.saving"
          :disabled="!state.ready || state.loading"
          @click="save"
          >{{ state.pending ? "重试保存" : "保存并应用" }}</el-button
        >
      </div></template
    >
  </el-dialog>
</template>
<style scoped>
.template-note {
  color: #65786f;
  font-size: 12px;
  line-height: 1.7;
  margin: 0 0 10px;
}
.template-sequence {
  padding: 10px;
  background: #ecf6f0;
  color: #21664f;
  border-radius: 6px;
}
.template-fields {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
.template-scroll {
  overflow: auto;
  max-height: 55vh;
}
.template-table {
  width: 100%;
  min-width: 880px;
  border-collapse: collapse;
  font-size: 12px;
}
.template-table th {
  background: #f3f7f5;
  text-align: left;
  padding: 10px;
  position: sticky;
  top: 0;
}
.template-table td {
  border-bottom: 1px solid #e4ebe6;
  padding: 10px;
  vertical-align: middle;
}
.template-table .entry-column-setting {
  display: table-row;
}
.template-table input:not([type="checkbox"]),
.template-table select {
  box-sizing: border-box;
  height: 32px;
  padding: 4px 7px;
  border: 1px solid #d3dfd7;
  border-radius: 4px;
  max-width: 160px;
  width: 100%;
}
.template-table label {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 4px 0;
}
.template-width {
  display: flex;
  gap: 4px;
  max-width: 160px;
}
.template-width input {
  min-width: 65px;
}
.template-order {
  display: flex;
  gap: 4px;
  margin-top: 5px;
}
.template-order button {
  font-size: 11px;
  border: 1px solid #d6e3db;
  background: white;
  border-radius: 4px;
  color: #537365;
}
.template-drag {
  cursor: grab;
  font-size: 20px;
  padding-right: 7px;
}
.template-footer {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.template-footer span {
  flex: 1;
}
.template-footer .el-button + .el-button {
  margin: 0;
}
</style>
