<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { session } from "./api";
import { resolveEntryFile } from "./entryFileLookup";
import type { EntryFileRow, EntryMatchBy } from "./entryFiles";
import EntryImportPreview from "./EntryImportPreview.vue";
import {
  parseEntryPaste,
  type EntryLine,
  type EntryChannel,
} from "./documentEntry";
const props = defineProps<{
  entity: string;
  channel: EntryChannel;
  remaining: number;
}>();
const emit = defineEmits<{ close: []; apply: [lines: EntryLine[]] }>();
const raw = ref(""),
  matchBy = ref<EntryMatchBy>("code"),
  rows = ref<EntryFileRow[]>([]),
  error = ref(""),
  busy = ref(false);
let epoch = 0;
let controller: AbortController | undefined;
function cancel() {
  epoch++;
  controller?.abort();
  rows.value = [];
  busy.value = false;
  error.value = "";
}
onUnmounted(cancel);
watch(
  () => [
    props.entity,
    props.channel,
    session.value?.tenant_id,
    session.value?.principal_id,
    session.value?.context_generation,
    session.value?.permission_version,
  ],
  cancel,
);
watch([raw, matchBy], cancel);
function insertSeparator(event: KeyboardEvent) {
  if (
    event.key !== "Tab" ||
    event.shiftKey ||
    event.ctrlKey ||
    event.altKey ||
    event.metaKey ||
    event.isComposing ||
    event.keyCode === 229 ||
    busy.value
  )
    return;
  event.preventDefault();
  const input = event.currentTarget as HTMLTextAreaElement;
  const length =
    input.value.length - (input.selectionEnd - input.selectionStart);
  if (length >= input.maxLength) return;
  // Replace only the selected text and keep the caret after the real TSV separator.
  input.setRangeText("\t", input.selectionStart, input.selectionEnd, "end");
  raw.value = input.value;
}
async function preview() {
  if (busy.value) return;
  const current = ++epoch;
  error.value = "";
  let mapped: EntryFileRow[];
  try {
    mapped = rows.value.length
      ? rows.value.map((r) => ({ ...r, line: { ...r.line } }))
      : parseEntryPaste(
          raw.value,
          matchBy.value === "barcode" ? "商品条码" : "SKU编码",
        ).map((r) => ({
          sourceRow: r.row,
          code: r.code,
          matchBy: matchBy.value,
          unit: "",
          packageId: "",
          line: r.line,
          sourceErrors: r.error ? [r.error] : [],
          errors: [],
        }));
    if (mapped.length > props.remaining)
      throw new Error(`当前最多还能添加 ${props.remaining} 行`);
  } catch (e) {
    error.value = (e as Error).message;
    return;
  }
  busy.value = true;
  rows.value.forEach((r) => {
    r.needsCheck = true;
  });
  controller?.abort();
  controller = new AbortController();
  try {
    await resolveEntryFile(
      mapped,
      props.entity,
      props.channel,
      controller.signal,
    );
    if (current !== epoch) return;
    rows.value = mapped;
  } catch {
    if (current === epoch) {
      controller.abort();
      error.value = "查询商品失败，尚未填入任何行，可重试预览";
    }
  } finally {
    if (current === epoch) busy.value = false;
  }
}
</script>
<template>
  <el-dialog
    :model-value="true"
    append-to-body
    title="粘贴多行商品"
    width="min(1280px, 96vw)"
    class="business-dialog entry-import-dialog"
    top="4vh"
    :close-on-click-modal="false"
    @close="emit('close')"
  >
    <fieldset class="paste-match" :disabled="busy">
      <legend>第一列识别方式</legend>
      <label
        ><input
          v-model="matchBy"
          type="radio"
          value="code"
        />商品编码（SKU编码）</label
      >
      <label
        ><input
          v-model="matchBy"
          type="radio"
          value="barcode"
        />商品条码（自动匹配包装）</label
      >
    </fieldset>
    <p>
      从表格复制后粘贴，按列填写：<b
        >{{
          matchBy === "barcode" ? "商品条码" : "SKU编码"
        }}、数量、含税单价、税率（可选，默认0.13）</b
      >。
    </p>
    <p class="form-note">
      每行一个商品，以制表符分列，不含表头。{{
        matchBy === "barcode"
          ? "按条码对应单位录入数量和单价，不会自动换算价格。"
          : "默认按基本单位录入，可在预览中填写其他交易单位并核对数量、价格。"
      }}相同商品保留独立行；条码保留前导零与大小写。
    </p>
    <p id="entry-paste-keys" class="paste-keys">
      手工输入时：Tab 分列，Enter 换行；Shift + Tab 离开输入区。
    </p>
    <textarea
      v-model="raw"
      aria-label="多行商品数据"
      aria-describedby="entry-paste-keys"
      :rows="rows.length ? 3 : 6"
      maxlength="30000"
      :disabled="busy"
      class="entry-paste-text"
      :placeholder="`${matchBy === 'barcode' ? '0690000000001' : 'SKU-001'}\t10\t12.50\t0.13`"
      @keydown="insertSeparator"
    />
    <p v-if="error" role="alert" class="paste-error">{{ error }}</p>
    <el-button :loading="busy" @click="preview">{{
      rows.length ? "重新校验" : "校验并预览"
    }}</el-button>
    <EntryImportPreview
      v-if="rows.length"
      :rows="rows"
      :disabled="busy"
      label="粘贴导入预览"
      source="paste"
    />
    <template #footer>
      <el-button @click="emit('close')">返回</el-button
      ><el-button
        type="primary"
        :disabled="
          busy ||
          !!error ||
          !rows.length ||
          rows.some((r) => r.errors.length || r.needsCheck) ||
          rows.length > remaining
        "
        @click="
          emit(
            'apply',
            rows.map((r) => r.line),
          )
        "
        >确认填入 {{ rows.length }} 行</el-button
      >
    </template>
  </el-dialog>
</template>
<style scoped>
.paste-match {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 8px 12px;
}
.paste-match legend {
  font-size: 12px;
  color: var(--text-muted, #65786f);
}
.entry-paste-text {
  width: 100%;
  box-sizing: border-box;
  padding: 12px;
  border: 1px solid #c6d8ce;
  border-radius: 8px;
  margin: 12px 0;
  font: 14px/1.6 monospace;
  tab-size: 12;
  white-space: pre;
  overflow-wrap: normal;
}
.paste-keys {
  margin: 0;
  color: #4c6c65;
  font-size: 13px;
}
.paste-error {
  color: #b33b2b;
}
</style>
