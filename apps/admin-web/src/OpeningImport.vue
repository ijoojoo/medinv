<script setup lang="ts">
import { businessToday } from "./dates";
import { computed, onUnmounted, ref } from "vue";
import {
  MAX_IMPORT_BYTES,
  openingTemplate,
  previewOpening,
  type ImportSku,
  type ImportLocation,
  type ImportPreview,
  type OpeningInput,
} from "./openingImport";
const props = defineProps<{
  warehouse: string;
  skus: ImportSku[];
  locations: ImportLocation[];
  disabled: boolean;
}>();
const emit = defineEmits<{
  apply: [lines: OpeningInput[], filename: string];
  pending: [value: boolean];
}>();
const preview = ref<ImportPreview | null>(null),
  error = ref(""),
  filename = ref(""),
  reading = ref(false);
const fileInput = ref<HTMLInputElement | null>(null),
  errorsOnly = ref(false);
const visibleRows = computed(
  () =>
    preview.value?.rows.filter(
      (row) => !errorsOnly.value || row.errors.length,
    ) || [],
);
let generation = 0;
onUnmounted(() => {
  generation++;
  emit("pending", false);
});
function clear() {
  generation++;
  preview.value = null;
  error.value = "";
  filename.value = "";
  reading.value = false;
  errorsOnly.value = false;
  emit("pending", false);
}
function download() {
  const url = URL.createObjectURL(
    new Blob([openingTemplate()], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "期初库存导入模板.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function choose(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0];
  input.value = "";
  if (!file || props.disabled) return;
  clear();
  const current = ++generation;
  filename.value = file.name;
  reading.value = true;
  emit("pending", true);
  try {
    if (!file.name.toLowerCase().endsWith(".csv"))
      throw new Error("请选择 CSV 文件；Excel 文件请另存为 CSV UTF-8 后导入。");
    if (file.size > MAX_IMPORT_BYTES)
      throw new Error("文件超过512 KB，请使用最多200行的期初CSV清单。");
    const bytes = await file.arrayBuffer();
    if (current !== generation) return;
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      throw new Error("文件不是 UTF-8 编码，请另存为 CSV UTF-8 后重试。");
    }
    const today = businessToday();
    preview.value = previewOpening(
      text,
      props.skus,
      props.locations,
      props.warehouse,
      today,
    );
    errorsOnly.value = preview.value.errorCount > 0;
  } catch (e) {
    if (current === generation)
      error.value =
        e instanceof Error ? e.message : "文件读取失败，请重新选择。";
  } finally {
    if (current === generation) reading.value = false;
  }
}
function apply() {
  if (
    !preview.value ||
    preview.value.errorCount ||
    props.disabled ||
    reading.value
  )
    return;
  emit(
    "apply",
    preview.value.rows.map((row) => ({ ...row.input })),
    filename.value,
  );
  clear();
}
</script>

<template>
  <section class="opening-import" aria-label="期初库存批量导入">
    <div class="detail-toolbar">
      <strong>从原库存清单导入</strong>
      <el-button :disabled="disabled" @click="download">下载期初模板</el-button>
      <el-button :disabled="disabled || !warehouse" @click="fileInput?.click()"
        >选择期初文件</el-button
      >
      <input
        ref="fileInput"
        class="import-file"
        aria-label="期初CSV文件"
        type="file"
        accept=".csv,text/csv"
        :disabled="disabled || !warehouse"
        @change="choose"
      />
    </div>
    <p class="form-note">
      先选仓库，再导入 CSV UTF-8 文件（最多200行、512
      KB）。SKU编码、库位编码和批号请按文本保存；日期使用
      YYYY-MM-DD，数量按基本单位填写，成本为该行总额。文件仅在本页预览。
    </p>
    <p v-if="filename" class="form-note">当前文件：{{ filename }}</p>
    <p v-if="reading" role="status">正在读取并核对清单…</p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <template v-if="preview">
      <el-alert
        :type="preview.errorCount ? 'error' : 'success'"
        :closable="false"
        show-icon
        :title="
          preview.errorCount
            ? `共${preview.rows.length}行，其中${preview.errorCount}行需更正；尚未填入清单。`
            : `校验通过：${preview.rows.length}行，期初成本合计 ${preview.costTotal} 元。`
        "
      />
      <el-checkbox v-if="preview.errorCount" v-model="errorsOnly"
        >只看需要更正的行</el-checkbox
      >
      <el-table
        :data="visibleRows"
        max-height="400"
        data-testid="opening-import-preview"
      >
        <el-table-column prop="row" label="文件行" width="75" />
        <el-table-column prop="sku_code" label="SKU编码" min-width="130" />
        <el-table-column prop="name" label="商品" min-width="140" />
        <el-table-column prop="location_code" label="库位" min-width="90" />
        <el-table-column prop="input.lot_no" label="批号" min-width="110" />
        <el-table-column prop="input.mfg_date" label="生产日期" width="115" />
        <el-table-column
          prop="input.expiry_date"
          label="有效期至"
          width="115"
        />
        <el-table-column label="数量" min-width="100"
          ><template #default="{ row }"
            >{{ row.input.qty }} {{ row.base_unit }}</template
          ></el-table-column
        >
        <el-table-column
          prop="input.cost_amount"
          label="成本（元）"
          min-width="120"
        />
        <el-table-column label="核对结果" min-width="250"
          ><template #default="{ row }"
            ><span :class="{ 'import-error': row.errors.length }">{{
              row.errors.join("；") || "通过"
            }}</span></template
          ></el-table-column
        >
      </el-table>
      <p class="form-note">
        “填入清单”将替换下方尚未保存的全部商品行。可继续核对修改，保存后仍需独立审核和启用；预览不产生库存。
      </p>
      <el-button
        type="primary"
        :disabled="disabled || !!preview.errorCount || reading"
        @click="apply"
        >核对后填入清单（替换现有行）</el-button
      >
    </template>
    <el-button v-if="filename" :disabled="disabled" @click="clear"
      >关闭导入预览</el-button
    >
  </section>
</template>

<style scoped>
.opening-import {
  border: 1px solid #dce6e0;
  padding: 18px;
  border-radius: 8px;
  margin: 16px 0;
}
.import-file {
  display: none;
}
.import-error {
  color: #b43b36;
}
.opening-import .el-alert {
  margin: 12px 0;
}
.opening-import .el-table {
  margin: 12px 0;
}
</style>
