<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { session } from "./api";
import type { EntryChannel, EntryLine } from "./documentEntry";
import {
  ENTRY_FILE_BYTES,
  downloadEntryCsv,
  entryErrorCsv,
  entryFileTemplate,
  parseEntryFile,
  type EntryFileRow,
  type EntryImportMode,
} from "./entryFiles";
import { resolveEntryFile } from "./entryFileLookup";
import EntryImportPreview from "./EntryImportPreview.vue";
const props = defineProps<{
  entity: string;
  channel: EntryChannel;
  remaining: number;
  currentCount: number;
  disabled?: boolean;
}>();
const emit = defineEmits<{
  close: [];
  apply: [lines: EntryLine[], mode: EntryImportMode];
}>();
const mode = ref<EntryImportMode>("append"),
  rows = ref<EntryFileRow[]>([]),
  error = ref(""),
  filename = ref(""),
  busy = ref(false),
  errorsOnly = ref(false),
  replaceConfirmed = ref(false);
const errorCount = computed(
  () => rows.value.filter((r) => r.errors.length || r.needsCheck).length,
);
const visibleRows = computed(() =>
  rows.value.filter(
    (r) => !errorsOnly.value || r.errors.length || r.needsCheck,
  ),
);
const limit = computed(() => (mode.value === "append" ? props.remaining : 50));
let epoch = 0,
  controller: AbortController | undefined;
function clear() {
  epoch++;
  controller?.abort();
  rows.value = [];
  error.value = "";
  busy.value = false;
  replaceConfirmed.value = false;
}
onUnmounted(clear);
watch(
  () => [
    props.entity,
    props.channel,
    session.value?.tenant_id,
    session.value?.principal_id,
    session.value?.context_generation,
    session.value?.permission_version,
  ],
  clear,
);
watch(
  () => props.disabled,
  (value) => {
    if (value) clear();
  },
);
watch(mode, () => {
  replaceConfirmed.value = false;
});
async function choose(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0];
  input.value = "";
  if (!file || props.disabled) return;
  clear();
  const current = epoch;
  controller = new AbortController();
  const signal = controller.signal;
  busy.value = true;
  filename.value = file.name;
  try {
    if (!file.name.toLowerCase().endsWith(".csv"))
      throw new Error(
        "请选择 CSV UTF-8 文件；Excel 工作簿请另存为此格式后导入。",
      );
    if (file.size > ENTRY_FILE_BYTES)
      throw new Error("文件超过256 KB，每单最多50行。");
    const bytes = await file.arrayBuffer();
    signal.throwIfAborted();
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      throw new Error("文件不是UTF-8编码，请另存为 CSV UTF-8 后重试。");
    }
    const parsed = parseEntryFile(text);
    await resolveEntryFile(parsed, props.entity, props.channel, signal);
    if (current !== epoch) return;
    rows.value = parsed;
    errorsOnly.value = errorCount.value > 0;
  } catch (e) {
    if (current === epoch) {
      error.value =
        e instanceof Error ? e.message : "读取或校验失败，请重新选择文件重试";
      controller.abort();
    }
  } finally {
    if (current === epoch) busy.value = false;
  }
}
async function recheck() {
  if (props.disabled || busy.value || !rows.value.length) return;
  const current = ++epoch;
  controller?.abort();
  controller = new AbortController();
  busy.value = true;
  error.value = "";
  replaceConfirmed.value = false;
  rows.value.forEach((r) => {
    r.needsCheck = true;
  });
  const parsed = rows.value.map((r) => ({ ...r, line: { ...r.line } }));
  try {
    await resolveEntryFile(
      parsed,
      props.entity,
      props.channel,
      controller.signal,
    );
    if (current === epoch) {
      rows.value = parsed;
      errorsOnly.value = errorCount.value > 0;
    }
  } catch (e) {
    if (current === epoch) {
      error.value = e instanceof Error ? e.message : "校验失败，请重试";
      controller.abort();
    }
  } finally {
    if (current === epoch) busy.value = false;
  }
}
function apply() {
  if (
    props.disabled ||
    busy.value ||
    error.value ||
    !rows.value.length ||
    errorCount.value ||
    rows.value.length > limit.value ||
    (mode.value === "replace" &&
      props.currentCount > 0 &&
      !replaceConfirmed.value)
  )
    return;
  emit(
    "apply",
    rows.value.map((r) => ({ ...r.line })),
    mode.value,
  );
}
</script>
<template>
  <el-dialog
    :model-value="true"
    append-to-body
    title="导入商品明细"
    width="min(1280px, 96vw)"
    class="business-dialog entry-import-dialog"
    top="4vh"
    :close-on-click-modal="false"
    @close="emit('close')"
  >
    <p class="file-note">
      CSV UTF-8，最多50行、256
      KB。选择商品编码模板或条码模板，填写数量、含税单价；税率按小数填写，空白默认0.13。编码与条码列请按文本保存，避免前导零丢失。
    </p>
    <p class="file-note">
      商品编码模式中，单位留空按基本单位；条码模式中，单位留空按条码对应包装。同一行只填SKU编码或商品条码。全部校验通过后才填入，不直接保存或审核单据。
    </p>
    <div class="file-tools">
      <el-button
        :disabled="disabled"
        @click="downloadEntryCsv(entryFileTemplate(), '商品明细导入模板.csv')"
        >下载导入模板</el-button
      >
      <el-button
        :disabled="disabled"
        @click="
          downloadEntryCsv(entryFileTemplate('barcode'), '商品条码导入模板.csv')
        "
        >下载条码模板</el-button
      >
      <label class="file-picker"
        >选择文件<input
          type="file"
          accept=".csv,text/csv"
          aria-label="商品明细CSV文件"
          :disabled="disabled"
          @change="choose" /></label
      ><span>{{ filename }}</span>
    </div>
    <fieldset :disabled="disabled" class="file-mode">
      <legend>填入方式</legend>
      <label
        ><input
          v-model="mode"
          type="radio"
          value="append"
        />追加到现有明细（还能填入 {{ remaining }} 行）</label
      ><label
        ><input v-model="mode" type="radio" value="replace" />替换当前明细（当前
        {{ currentCount }} 行）</label
      >
    </fieldset>
    <p v-if="busy" role="status">正在读取文件并校验商品、单位…</p>
    <p v-if="error" role="alert" class="file-error">
      {{ error }}。尚未填入任何行。
    </p>
    <template v-if="rows.length"
      ><div class="file-tools">
        <b>共 {{ rows.length }} 行，{{ errorCount }} 行需修正</b
        ><label><input v-model="errorsOnly" type="checkbox" />只看错误行</label
        ><el-button :loading="busy" :disabled="disabled" @click="recheck"
          >重新校验</el-button
        ><el-button
          v-if="errorCount"
          :disabled="disabled || busy"
          @click="downloadEntryCsv(entryErrorCsv(rows), '商品明细-待修正.csv')"
          >下载待修正清单</el-button
        >
      </div>
      <p v-if="rows.length > limit" class="file-error" role="alert">
        本次{{ mode === "append" ? "追加" : "替换" }}最多
        {{ limit }} 行，请调整文件或填入方式。
      </p>
      <EntryImportPreview
        :rows="visibleRows"
        :disabled="disabled || busy"
        label="文件导入预览"
        @change="replaceConfirmed = false"
      />
      <label v-if="mode === 'replace' && currentCount > 0" class="file-confirm"
        ><input
          v-model="replaceConfirmed"
          type="checkbox"
          :disabled="disabled"
        />确认替换当前
        {{ currentCount }} 行；填入后、继续编辑前可以撤销导入。</label
      >
    </template>
    <template #footer
      ><el-button @click="emit('close')">返回</el-button
      ><el-button
        type="primary"
        :disabled="
          disabled ||
          busy ||
          !!error ||
          !rows.length ||
          !!errorCount ||
          rows.length > limit ||
          (mode === 'replace' && currentCount > 0 && !replaceConfirmed)
        "
        @click="apply"
        >确认{{ mode === "append" ? "追加" : "替换" }}
        {{ rows.length }} 行</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.file-note {
  color: #65786f;
  font-size: 12px;
  line-height: 1.8;
  margin: 0 0 8px;
}
.file-tools {
  display: flex;
  gap: 14px;
  align-items: center;
  flex-wrap: wrap;
  margin: 12px 0;
}
.file-tools > span {
  max-width: 300px;
  overflow-wrap: anywhere;
  font-size: 12px;
  color: #65786f;
}
.file-picker {
  display: flex;
  gap: 8px;
  align-items: center;
}
.file-picker input {
  max-width: 240px;
}
.file-mode {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  border: 1px solid #dce7e1;
  border-radius: 6px;
  margin: 12px 0;
  padding: 10px;
}
.file-mode legend {
  font-size: 12px;
  color: #65786f;
}
.file-error {
  color: #ad3f30;
}
.file-unit {
  display: block;
  color: #65786f;
  margin-top: 5px;
}
.file-confirm {
  display: block;
  margin-top: 16px;
  line-height: 1.7;
}
</style>
