<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import { entryCsv, downloadEntryCsv } from "./entryFiles";
const emit = defineEmits<{ failure: [error: unknown]; close: []; saved: [] }>();
interface PreviewRow {
  row: number;
  values: Record<string, string>;
  errors: string[];
}
interface PreviewResult {
  rows: PreviewRow[];
  can_import: boolean;
}
const headers = ref<string[]>([]),
  content = ref(""),
  filename = ref(""),
  loading = ref(false);
const preview = ref<{ rows: PreviewRow[]; can_import: boolean } | null>(null);
const results = ref<{ row: number; code: string; state: string }[]>([]);
const onlyErrors = ref(false);
const visibleRows = computed(
  () =>
    preview.value?.rows.filter((r) => !onlyErrors.value || r.errors.length) ||
    [],
);
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(() => ({
    dirty: !!content.value,
    busy: busy.value || loading.value,
  })),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function loadTemplate() {
  try {
    headers.value = (
      await request<{ headers: string[] }>("/api/v1/catalog/product-import")
    ).headers;
  } catch (e) {
    emit("failure", e);
  }
}
async function check() {
  const current = ++epoch;
  loading.value = true;
  preview.value = null;
  try {
    const r = await request<PreviewResult>(
      "/api/v1/catalog/product-import/preview",
      { method: "POST", body: JSON.stringify({ content: content.value }) },
    );
    if (current === epoch) {
      preview.value = r;
      onlyErrors.value = !r.can_import;
    }
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function choose(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0];
  input.value = "";
  if (!file || busy.value || loading.value) return;
  preview.value = null;
  results.value = [];
  content.value = "";
  filename.value = file.name;
  loading.value = true;
  try {
    if (!file.name.toLowerCase().endsWith(".csv") || file.size > 262144)
      throw new Error("请选择256 KB以内的CSV UTF-8文件，每次最多100条新商品。");
    const text = new TextDecoder("utf-8", { fatal: true }).decode(
      await file.arrayBuffer(),
    );
    content.value = text;
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
  if (content.value) await check();
}
function errorsFile() {
  if (!preview.value) return;
  // Include every original value; rows with an empty code remain eligible for automatic numbering.
  const keys: Record<string, string> = {
    商品类别: "category",
    商品编码: "code",
    名称: "name",
    生产企业: "manufacturer_name",
    规格型号: "specification",
    基本单位: "base_unit",
    数量小数位: "qty_scale",
    批准注册备案编号: "approval_number",
    资质生效日期: "valid_from",
    资质截止日期: "valid_until",
    器械管理类别: "device_class",
    药品销售分类: "drug_sale_class",
    药品通用名: "generic_name",
    剂型: "dosage_form",
    器械型号: "device_model",
    商品名品牌: "trade_name",
    检索别名助记码: "search_alias",
    生产企业地址: "manufacturer_address",
    储存条件: "storage_conditions",
    上市许可持有人: "authorization_holder",
    上市许可持有人地址: "authorization_holder_address",
    注册人备案人: "registrant_name",
    注册人备案人地址: "registrant_address",
    药品经营范围类别: "drug_business_scope",
    器械经营范围类别: "device_business_scope",
    质量管控备注: "quality_note",
  };
  downloadEntryCsv(
    entryCsv(
      [...headers.value, "校验结果"],
      preview.value.rows
        .filter((r) => r.errors.length)
        .map((r) => [
          ...headers.value.map((h) => r.values[keys[h]] || ""),
          r.errors.join("；"),
        ]),
    ),
    "商品资料待修正清单.csv",
  );
}
async function submit() {
  if (!preview.value?.can_import || loading.value) return;
  await execute<{ items: typeof results.value }>(
    "/api/v1/catalog/product-import",
    { content: content.value },
    (r) => {
      results.value = r.items;
      content.value = "";
      preview.value = null;
      emit("saved");
    },
  );
}
onMounted(loadTemplate);
</script>
<template>
  <section class="panel" aria-label="商品资料批量导入">
    <div class="business-toolbar">
      <el-button :disabled="busy || loading" @click="emit('close')"
        >← 返回资料列表</el-button
      >
      <h2>商品资料批量导入</h2>
    </div>
    <p class="form-note">
      按当前字段、字典及编码规则预检。只新增商品，已有编码或相同商品会提示核对。全部行通过后统一保存为待审核档案，有任一错误则整批不写入。
    </p>
    <div class="business-toolbar">
      <el-button
        :disabled="!headers.length || busy || loading"
        @click="downloadEntryCsv(entryCsv(headers, []), '商品资料导入模板.csv')"
        >下载商品模板</el-button
      >
      <label
        >选择商品CSV文件<input
          type="file"
          accept=".csv"
          :disabled="busy || loading"
          @change="choose"
      /></label>
      <el-button v-if="content" :disabled="busy || loading" @click="check"
        >重新预检</el-button
      >
      <el-button
        v-if="content"
        :disabled="busy || loading"
        @click="
          content = '';
          preview = null;
          filename = '';
        "
        >清空导入预览</el-button
      >
    </div>
    <p class="form-note">
      商品类别填写“药品 / 医疗器械”；日期填写
      YYYY-MM-DD。商品编码可留空自动生成，数量小数位默认0。字典字段须符合企业选值规则。Excel请另存为CSV
      UTF-8，每次最多100条、256 KB。
    </p>
    <p v-if="loading">正在读取并核对资料…</p>
    <template v-if="preview">
      <div class="business-toolbar">
        <strong>{{ filename }} · {{ preview.rows.length }} 行</strong
        ><label><input type="checkbox" v-model="onlyErrors" />只看错误行</label
        ><el-button
          :disabled="!preview.rows.some((r) => r.errors.length) || busy"
          @click="errorsFile"
          >下载待修正清单</el-button
        >
      </div>
      <el-table :data="visibleRows" row-key="row" max-height="520"
        ><el-table-column
          prop="row"
          label="文件行"
          width="85"
        /><el-table-column label="商品类别" width="120"
          ><template #default="{ row }">{{
            row.values.category === "DRUG"
              ? "药品"
              : row.values.category === "DEVICE"
                ? "医疗器械"
                : row.values.category
          }}</template></el-table-column
        ><el-table-column label="商品编码" min-width="150"
          ><template #default="{ row }">{{
            row.values.code || "保存时自动生成"
          }}</template></el-table-column
        ><el-table-column label="名称 / 规格" min-width="190"
          ><template #default="{ row }"
            >{{ row.values.name }} · {{ row.values.specification }}</template
          ></el-table-column
        ><el-table-column label="校验结果" min-width="270"
          ><template #default="{ row }"
            ><span :class="{ error: row.errors.length }">{{
              row.errors.join("；") || "通过，待确认导入"
            }}</span></template
          ></el-table-column
        ></el-table
      >
      <div class="form-actions">
        <span class="form-note">正式提交会再次校验；预检不占用自动编码。</span
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="!preview.can_import || loading"
          @click="submit"
          >确认导入为待审核资料</el-button
        >
      </div>
    </template>
    <template v-if="results.length"
      ><h3>已导入 {{ results.length }} 条商品，等待独立审核</h3>
      <el-button
        @click="
          downloadEntryCsv(
            entryCsv(
              ['文件行', '商品编码', '状态'],
              results.map((r) => [String(r.row), r.code, '待审核']),
            ),
            '商品导入结果.csv',
          )
        "
        >导出生成编码</el-button
      ><el-table :data="results"
        ><el-table-column prop="row" label="文件行" /><el-table-column
          prop="code"
          label="商品编码"
        /><el-table-column label="状态"
          ><template #default>待审核</template></el-table-column
        ></el-table
      ></template
    >
  </section>
</template>
<style scoped>
.error {
  color: var(--el-color-danger);
}
h2 {
  font-size: 19px;
}
input[type="checkbox"] {
  width: auto;
}
</style>
