<script setup lang="ts">
import type { EntryFileRow } from "./entryFiles";
defineProps<{
  rows: EntryFileRow[];
  disabled?: boolean;
  label: string;
  source?: "paste" | "file";
}>();
const emit = defineEmits<{ change: [row: EntryFileRow] }>();
function edit(row: EntryFileRow) {
  row.needsCheck = true;
  row.matchedUnit = row.conversion = row.baseQuantity = "";
  delete row.line.product;
  row.line.sku_id = row.line.packaging_id = "";
  emit("change", row);
}
</script>
<template>
  <p class="preview-note">
    可直接修改下表，再点“重新校验”。数量与含税单价均按交易单位填写，确认前请核对折合数量。
    {{
      source === "paste"
        ? "以下表校验结果为准；修改上方原文或识别方式会清空预览及修正。列数错误请修改原文。"
        : "文件列数或版本错误需修改文件后重选。"
    }}
  </p>
  <el-table
    :data="rows"
    max-height="350"
    :aria-label="label"
    class="import-preview"
  >
    <el-table-column prop="sourceRow" label="来源行" width="70" />
    <el-table-column label="识别方式 / 编码" min-width="185">
      <template #default="{ row }">
        <select
          v-model="row.matchBy"
          :aria-label="`第${row.sourceRow}行识别方式`"
          :disabled="disabled"
          @change="edit(row)"
        >
          <option value="code">商品编码（SKU编码）</option>
          <option value="barcode">商品条码</option>
        </select>
        <input
          v-model="row.code"
          :aria-label="`第${row.sourceRow}行编码或条码`"
          :maxlength="row.matchBy === 'barcode' ? 128 : 100"
          :disabled="disabled"
          autocomplete="off"
          @input="edit(row)"
        />
      </template>
    </el-table-column>
    <el-table-column label="商品 / 包装核对" min-width="200">
      <template #default="{ row }">
        <b>{{
          row.line.product?.name ||
          (row.needsCheck ? "修改后待校验" : "尚未匹配")
        }}</b>
        <small v-if="row.line.product"
          >{{ row.line.product.code }} ·
          {{ row.line.product.specification }}</small
        >
        <small v-if="row.matchedUnit"
          >数量、单价按 <strong>{{ row.matchedUnit }}</strong> 填写</small
        >
        <small v-if="row.conversion">{{ row.conversion }}</small>
        <small v-if="row.baseQuantity">折合 {{ row.baseQuantity }}</small>
      </template>
    </el-table-column>
    <el-table-column label="交易单位" min-width="125">
      <template #default="{ row }">
        <input
          v-model="row.unit"
          :aria-label="`第${row.sourceRow}行交易单位`"
          :placeholder="row.matchBy === 'barcode' ? '按条码包装' : '按基本单位'"
          maxlength="80"
          :disabled="disabled"
          @input="edit(row)"
        />
        <details v-if="row.packageId">
          <summary>指定包装</summary>
          <input
            v-model="row.packageId"
            :aria-label="`第${row.sourceRow}行包装标识`"
            :disabled="disabled"
            @input="edit(row)"
          />
          <small>可清空，改按单位或条码匹配</small>
        </details>
      </template>
    </el-table-column>
    <el-table-column label="数量" min-width="100"
      ><template #default="{ row }">
        <input
          v-model="row.line.qty"
          :aria-label="`第${row.sourceRow}行数量`"
          inputmode="decimal"
          maxlength="21"
          :disabled="disabled"
          @input="edit(row)"
        /> </template
    ></el-table-column>
    <el-table-column label="含税单价" min-width="110"
      ><template #default="{ row }">
        <input
          v-model="row.line.unit_price"
          :aria-label="`第${row.sourceRow}行含税单价`"
          inputmode="decimal"
          maxlength="21"
          :disabled="disabled"
          @input="edit(row)"
        /> </template
    ></el-table-column>
    <el-table-column label="税率（小数）" min-width="110"
      ><template #default="{ row }">
        <input
          v-model="row.line.tax_rate"
          :aria-label="`第${row.sourceRow}行税率`"
          inputmode="decimal"
          maxlength="21"
          :disabled="disabled"
          @input="edit(row)"
        /> </template
    ></el-table-column>
    <el-table-column label="校验结果" min-width="210"
      ><template #default="{ row }">
        <span
          :class="{ 'preview-error': row.errors.length || row.needsCheck }"
          >{{
            row.needsCheck
              ? "已修改，待重新校验"
              : row.errors.join("；") || "可填入"
          }}</span
        >
      </template></el-table-column
    >
  </el-table>
</template>
<style scoped>
.preview-note {
  font-size: 12px;
  color: var(--text-muted, #65786f);
  line-height: 1.7;
}
.import-preview input,
.import-preview select {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid var(--border, #ccd9e3);
  border-radius: 4px;
  padding: 5px 6px;
  background: var(--surface, #fff);
  color: inherit;
  font: inherit;
}
.import-preview select {
  margin-bottom: 5px;
}
.import-preview input:focus-visible,
.import-preview select:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}
.import-preview input:disabled,
.import-preview select:disabled {
  opacity: 0.65;
}
.import-preview small {
  display: block;
  color: var(--text-muted, #65786f);
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.import-preview b {
  font-weight: 500;
}
.import-preview details {
  margin-top: 5px;
  font-size: 12px;
}
.import-preview summary {
  cursor: pointer;
  color: var(--el-color-primary);
}
.preview-error {
  color: var(--el-color-danger);
}
</style>
