<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from "vue";
import {
  copyDocumentColumns,
  defaultDocumentColumns,
  reloadDocumentColumns,
  saveDocumentColumns,
  useDocumentColumns,
  type DocumentColumnState,
} from "./documentColumns";
const props = defineProps<{ state: DocumentColumnState }>();
const emit = defineEmits<{ close: [] }>();
const origin = document.activeElement as HTMLElement | null;
onUnmounted(() => {
  void nextTick(
    () => origin?.isConnected && origin.focus({ preventScroll: true }),
  );
});
const initial = props.state.pending
  ? JSON.parse(props.state.pending.body)
  : undefined;
const working = ref(
  copyDocumentColumns(
    initial?.columns || props.state.columns,
    props.state.fields,
  ),
);
const version = ref(initial?.expected_version || props.state.version);
const query = ref(""),
  group = ref("all"),
  dragging = ref(""),
  error = ref(""),
  notice = ref("");
const groups: Record<string, string> = {
  master: "商品资料",
  basic: "基本信息",
  price: "价格信息",
  quality: "质量管控",
  operations: "运营管控",
  extension: "扩展属性",
  integration: "第三方对接",
  custom: "自定义属性",
  common: "通用字段",
  stage: "本环节字段",
  derived: "计算结果",
};
const fields = computed(() =>
  props.state.fields.filter(
    (f) =>
      (group.value === "all" ||
        group.value === f.source ||
        group.value === f.group ||
        (group.value === "selected" &&
          working.value.find((c) => c.key === f.key)?.visible)) &&
      `${f.label} ${f.key} ${f.description} ${f.group_label || ""}`
        .toLowerCase()
        .includes(query.value.trim().toLowerCase()),
  ),
);
const chosen = computed(() => working.value.filter((c) => c.visible));
const meta = (key: string) => props.state.fields.find((f) => f.key === key)!;
const name = (key: string) => meta(key).label;
function toggle(key: string) {
  const c = working.value.find((c) => c.key === key)!;
  if (!c.required) c.visible = !c.visible;
}
function move(key: string, delta: number) {
  const i = chosen.value.findIndex((c) => c.key === key),
    target = chosen.value[i + delta];
  if (!target) return;
  const all = working.value,
    from = all.findIndex((c) => c.key === key),
    to = all.findIndex((c) => c.key === target.key);
  all.splice(to, 0, all.splice(from, 1)[0]!);
}
function drop(key: string) {
  const all = working.value,
    from = all.findIndex((c) => c.key === dragging.value),
    to = all.findIndex((c) => c.key === key);
  if (from >= 0 && to >= 0) all.splice(to, 0, all.splice(from, 1)[0]!);
  dragging.value = "";
}
async function save() {
  error.value = "";
  if (
    !working.value.every(
      (c) =>
        c.label.trim() &&
        Number.isInteger(c.width) &&
        (c.width_mode === "px"
          ? c.width >= (c.key === "product" ? 220 : 90) && c.width <= 600
          : c.width >= 1 && c.width <= 20),
    )
  ) {
    error.value = "请核对列名与宽度：固定宽度90–600，商品列至少220；比例1–20。";
    return;
  }
  if (await saveDocumentColumns(props.state, working.value, version.value))
    emit("close");
}
async function reload() {
  await reloadDocumentColumns(props.state);
  if (!props.state.error) {
    working.value = copyDocumentColumns(
      props.state.columns,
      props.state.fields,
    );
    version.value = props.state.version;
  }
}
const opposite = useDocumentColumns(
  computed(() => props.state.entity),
  computed(() => props.state.stage),
  computed(() => (props.state.mode === "editor" ? "view" : "editor")),
);
async function copyOther() {
  await reloadDocumentColumns(opposite.value);
  if (opposite.value.error) {
    error.value = opposite.value.error;
    return;
  }
  working.value = copyDocumentColumns(
    opposite.value.columns,
    props.state.fields,
  );
  notice.value =
    "已复制到本次编辑。仅保留适用字段，必填列自动保留；保存后生效，对方配置不变。";
}
</script>
<template>
  <el-dialog
    :model-value="true"
    title="配置单据明细列"
    width="1180px"
    append-to-body
    class="business-dialog document-column-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!state.saving"
    :show-close="!state.saving"
    @close="emit('close')"
  >
    <p class="column-setting-intro">
      {{ state.mode === "editor" ? "录入用" : "记录查看用" }} · 当前主体 ·
      仅我的配置
      <span
        >显示设置不改变业务数据。{{
          state.mode === "view"
            ? "查看列不参与键盘停留。"
            : "隐藏的业务值仍会保留并校验。"
        }}</span
      >
    </p>
    <p class="form-note">
      可选择商品资料全部页签及当前单据字段。商品资料为只读带入；参考价格不替代成交价，
      多个条码／平台编码显示其包装或账号范围。自定义字段发布后可选，受资料权限控制。
    </p>
    <el-alert
      v-if="error || state.error"
      :title="error || state.error"
      type="error"
      :closable="false"
    />
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-for="n in state.notices" :key="n" class="form-note">{{ n }}</p>
    <fieldset
      :disabled="state.saving || !!state.pending"
      class="column-settings-body"
    >
      <aside class="column-library">
        <input
          v-model="query"
          aria-label="搜索可选字段"
          placeholder="搜索名称、含义或字段编码"
        />
        <select v-model="group" aria-label="字段来源">
          <option value="all">全部字段</option>
          <option value="selected">已选择</option>
          <option v-for="(label, key) in groups" :key="key" :value="key">
            {{ label }}
          </option>
        </select>
        <div class="column-library-items">
          <label v-for="f in fields" :key="f.key" class="column-library-item">
            <input
              type="checkbox"
              :aria-label="f.label"
              :checked="working.find((c) => c.key === f.key)?.visible"
              :disabled="working.find((c) => c.key === f.key)?.required"
              @change="toggle(f.key)"
            />
            <span
              ><b>{{ f.label }}</b
              ><em
                >{{ groups[f.group || f.source]
                }}{{ f.required ? " · 必须保留" : "" }}</em
              ><small>{{ f.description }}</small></span
            >
          </label>
          <p v-if="!fields.length">没有匹配字段</p>
        </div>
      </aside>
      <div class="column-selected">
        <div class="column-selected-heading">
          <b>已选 {{ chosen.length }} 列</b
          ><span>拖动或使用前移 / 后移调整顺序</span>
        </div>
        <div class="column-selected-scroll">
          <div
            v-for="(c, i) in chosen"
            :key="c.key"
            class="entry-column-setting column-setting-row"
            :data-field="c.key"
            @dragover.prevent
            @drop.prevent="drop(c.key)"
          >
            <div class="column-setting-name">
              <span
                class="template-drag"
                draggable="true"
                :aria-label="name(c.key) + ' 拖动排序'"
                @dragstart="dragging = c.key"
                >⠿</span
              >
              <b>{{ name(c.key) }}</b
              ><small>{{
                groups[meta(c.key).group || meta(c.key).source]
              }}</small>
              <button
                type="button"
                :aria-label="name(c.key) + ' 前移'"
                :disabled="i === 0"
                @click="move(c.key, -1)"
              >
                ↑
              </button>
              <button
                type="button"
                :aria-label="name(c.key) + ' 后移'"
                :disabled="i === chosen.length - 1"
                @click="move(c.key, 1)"
              >
                ↓
              </button>
              <button
                type="button"
                :aria-label="name(c.key) + ' 移除'"
                :disabled="c.required"
                @click="c.visible = false"
              >
                移除
              </button>
            </div>
            <div class="column-setting-controls">
              <label
                >显示名称<input
                  v-model="c.label"
                  maxlength="60"
                  :aria-label="name(c.key) + ' 显示名称'"
              /></label>
              <label
                >宽度方式<select
                  v-model="c.width_mode"
                  @change="
                    c.width =
                      c.width_mode === 'px' ? meta(c.key).default.width : 1
                  "
                  :aria-label="name(c.key) + ' 宽度方式'"
                >
                  <option value="px">固定像素</option>
                  <option value="weight">剩余空间比例</option>
                </select></label
              >
              <label
                >宽度<input
                  type="number"
                  v-model.number="c.width"
                  :min="
                    c.width_mode === 'weight'
                      ? 1
                      : c.key === 'product'
                        ? 220
                        : 90
                  "
                  :max="c.width_mode === 'weight' ? 20 : 600"
                  :aria-label="name(c.key) + ' 宽度'"
              /></label>
              <label v-if="meta(c.key).type === 'number'"
                >小数位<select
                  v-model="c.decimals"
                  :aria-label="name(c.key) + ' 小数位'"
                >
                  <option :value="-1">原精度</option>
                  <option v-for="n in 7" :key="n" :value="n - 1">
                    {{ n - 1 }} 位
                  </option>
                </select></label
              >
              <label v-if="c.key === 'tax'"
                >税率格式<select v-model="c.tax_display" aria-label="税率 格式">
                  <option value="decimal">小数</option>
                  <option value="percent">百分比</option>
                </select></label
              >
            </div>
            <div class="column-setting-switches">
              <label v-if="state.mode === 'editor'"
                ><input
                  type="checkbox"
                  v-model="c.tab_stop"
                  :disabled="
                    !c.editable ||
                    !!state.fields.find((f) => f.key === c.key)?.stop_required
                  "
                  :aria-label="name(c.key) + ' 回车停留'"
                />回车 / Tab 停留</label
              >
              <label v-if="meta(c.key).type === 'number'"
                ><input
                  type="checkbox"
                  v-model="c.grouping"
                  :aria-label="name(c.key) + ' 千分位'"
                />千分位</label
              >
              <label v-if="meta(c.key).type === 'text'"
                ><input
                  type="checkbox"
                  v-model="c.wrap"
                  :aria-label="name(c.key) + ' 自动换行'"
                />自动换行</label
              >
            </div>
          </div>
        </div>
      </div>
    </fieldset>
    <template #footer
      ><div class="column-setting-footer">
        <el-button
          :disabled="state.saving || !!state.pending"
          @click="working = defaultDocumentColumns(state.fields)"
          >恢复默认</el-button
        >
        <el-button
          :disabled="state.saving || !!state.pending"
          @click="copyOther"
          >从{{
            state.mode === "editor" ? "记录查看" : "录入"
          }}配置复制</el-button
        >
        <el-button :disabled="state.saving" @click="reload">重新读取</el-button
        ><span />
        <el-button :disabled="state.saving" @click="emit('close')"
          >取消</el-button
        >
        <el-button type="primary" :loading="state.saving" @click="save">{{
          state.pending ? "继续确认保存" : "保存配置"
        }}</el-button>
      </div></template
    >
  </el-dialog>
</template>
<style>
.column-setting-intro {
  display: flex;
  gap: 20px;
  margin: 0 0 14px;
  color: var(--el-text-color-primary);
}
.column-setting-intro span,
.column-selected-heading span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.column-settings-body {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 18px;
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.column-library {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.column-library-items,
.column-selected-scroll {
  height: min(55vh, 550px);
  overflow: auto;
  overscroll-behavior: contain;
}
.column-library-item {
  display: flex;
  gap: 10px;
  padding: 12px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
}
.column-library-item input {
  width: 16px !important;
  height: 16px !important;
  flex-shrink: 0;
  margin-top: 3px;
}
.column-library-item b,
.column-library-item em,
.column-library-item small {
  display: block;
}
.column-library-item em {
  font-size: 11px;
  font-style: normal;
  color: var(--el-color-primary);
  margin: 3px 0;
}
.column-library-item small {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
.column-selected {
  min-width: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  overflow: hidden;
}
.column-selected-heading {
  display: flex;
  justify-content: space-between;
  padding: 12px;
  background: var(--el-fill-color-light);
}
.column-setting-row {
  padding: 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.column-setting-name {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.column-setting-name b {
  flex: 1;
}
.column-setting-name small {
  color: var(--el-text-color-secondary);
}
.column-setting-name button {
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  border-radius: 4px;
  padding: 3px 8px;
  cursor: pointer;
}
.column-setting-name button:disabled {
  opacity: 0.4;
}
.column-setting-controls {
  display: grid;
  grid-template-columns: 2fr 1.4fr 1fr 1fr;
  gap: 10px;
}
.column-setting-controls label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  min-width: 0;
}
.column-setting-controls input,
.column-setting-controls select,
.column-library > input,
.column-library > select {
  display: block;
  box-sizing: border-box;
  width: 100%;
  height: 32px;
  border: 1px solid var(--el-border-color);
  border-radius: 5px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  padding: 4px 8px;
  margin-top: 4px;
}
.column-setting-switches {
  display: flex;
  gap: 16px;
  margin-top: 9px;
  font-size: 12px;
}
.column-setting-switches label {
  display: flex;
  align-items: center;
  gap: 6px;
}
.column-setting-switches input {
  width: 14px !important;
  height: 14px !important;
}
.column-setting-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.column-setting-footer > span {
  flex: 1;
}
.column-setting-footer .el-button + .el-button {
  margin-left: 0;
}
.template-drag {
  cursor: grab;
}
@media (max-width: 800px) {
  .column-settings-body {
    grid-template-columns: 220px minmax(0, 1fr);
  }
  .column-setting-controls {
    grid-template-columns: 1fr 1fr;
  }
  .column-setting-intro {
    display: block;
  }
}
@media (max-width: 580px) {
  .column-settings-body {
    grid-template-columns: 1fr;
  }
  .column-library-items {
    height: 140px;
  }
  .column-selected-scroll {
    height: 35vh;
  }
}
</style>
