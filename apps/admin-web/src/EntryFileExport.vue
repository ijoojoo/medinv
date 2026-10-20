<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { request, session } from "./api";
import {
  blankEntryLine,
  type EntryChannel,
  type EntryColumn,
  type EntryLine,
  type EntryPackages,
  type EntryProduct,
} from "./documentEntry";
import { downloadEntryCsv, exportEntryFile } from "./entryFiles";
const props = defineProps<{
  lines: EntryLine[];
  columns: EntryColumn[];
  entity: string;
  channel: EntryChannel;
  disabled?: boolean;
}>();
const emit = defineEmits<{ close: [] }>();
const mode = ref("data"),
  busy = ref(false),
  error = ref("");
const count = computed(
  () => props.lines.filter((r) => !blankEntryLine(r)).length,
);
let controller: AbortController | undefined,
  epoch = 0;
function cancel() {
  epoch++;
  controller?.abort();
  busy.value = false;
}
onUnmounted(cancel);
watch(
  () => [
    props.entity,
    props.channel,
    session.value?.tenant_id,
    session.value?.principal_id,
    session.value?.context_generation,
  ],
  () => {
    cancel();
    emit("close");
  },
);
watch(
  () => props.disabled,
  (value) => {
    if (value) cancel();
  },
);
async function download() {
  if (props.disabled || busy.value) return;
  error.value = "";
  busy.value = true;
  const current = ++epoch;
  controller = new AbortController();
  const signal = controller.signal;
  const rows = props.lines
    .filter((r) => !blankEntryLine(r))
    .map((r) => ({ ...r, product: r.product ? { ...r.product } : undefined }));
  const columns = props.columns.map((c) => ({ ...c })),
    display = mode.value === "display";
  try {
    if (!rows.length) throw new Error("没有可导出的商品明细");
    if (rows.some((r) => !r.sku_id || r.search_text))
      throw new Error("请先确认所有商品检索文字，再导出明细");
    const skus = [...new Set(rows.map((r) => r.sku_id))],
      products = new Map<string, EntryProduct>(),
      packs = new Map<string, EntryPackages>();
    for (let i = 0; i < skus.length; i += 4) {
      signal.throwIfAborted();
      await Promise.all(
        skus.slice(i, i + 4).map(async (sku) => {
          const result = await request<{ items: EntryProduct[] }>(
            `/api/v1/catalog/entry-products?${new URLSearchParams({ entity_id: props.entity, channel: props.channel, sku_id: sku })}`,
            { signal },
          );
          const product = result.items.find((p) => p.id === sku);
          if (product) products.set(sku, product);
          if (rows.some((r) => r.sku_id === sku && r.packaging_id))
            packs.set(
              sku,
              await request<EntryPackages>(
                `/api/v1/catalog/skus/${sku}/packaging?approved_only=true`,
                { signal },
              ),
            );
        }),
      );
    }
    signal.throwIfAborted();
    if (current !== epoch) return;
    for (const row of rows) row.product = products.get(row.sku_id);
    const csv = exportEntryFile(rows, packs, display ? columns : undefined);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadEntryCsv(
      csv,
      `${props.channel === "purchase" ? "采购" : "销售"}-编辑草稿-${display ? "显示列" : "完整明细"}-${stamp}.csv`,
    );
    emit("close");
  } catch (e) {
    if (current === epoch) {
      error.value = e instanceof Error ? e.message : "导出失败，请重试";
      controller.abort();
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
    title="导出商品明细"
    width="620px"
    class="business-dialog"
    :close-on-click-modal="false"
    @close="emit('close')"
  >
    <p>
      导出当前编辑内容，共
      {{ count }} 行。文件标注为编辑草稿，不代表已审核或过账的正式单据。
    </p>
    <fieldset :disabled="busy || disabled" class="export-options">
      <label
        ><input v-model="mode" type="radio" value="data" /><b
          >完整明细（可再次导入）</b
        ><span
          >固定系统表头、完整原值、SKU编码与包装标识；不受列显隐、改名或显示精度影响。</span
        ></label
      ><label
        ><input v-model="mode" type="radio" value="display" /><b>按当前显示列</b
        ><span
          >按已保存列设置的名称、顺序和格式导出，用于查看与核对，不作为导入模板。</span
        ></label
      >
    </fieldset>
    <p class="export-note">
      CSV
      UTF-8。为保留编码前导零、数字精度并防止表格执行公式，部分单元格采用单引号文本保护；在Excel中可能显示该前缀，本系统再次导入时会还原。
    </p>
    <p v-if="error" role="alert" class="export-error">{{ error }}</p>
    <template #footer
      ><el-button @click="emit('close')">返回</el-button
      ><el-button
        type="primary"
        :loading="busy"
        :disabled="disabled || !count"
        @click="download"
        >下载CSV</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.export-options {
  padding: 0;
  border: 0;
  display: grid;
  gap: 12px;
}
.export-options label {
  padding: 14px;
  border: 1px solid #dce7e1;
  border-radius: 6px;
  display: grid;
  grid-template-columns: 20px 1fr;
  gap: 8px;
}
.export-options span {
  grid-column: 2;
  font-size: 12px;
  line-height: 1.8;
  color: #65786f;
}
.export-note {
  font-size: 12px;
  line-height: 1.8;
  color: #65786f;
}
.export-error {
  color: #ad3f30;
}
</style>
