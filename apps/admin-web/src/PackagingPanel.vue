<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { registerCatalogChanges } from "./catalogChanges";
import { request, session } from "./api";
import { can, useCommand } from "./business";
interface Package {
  id: string;
  version: string;
  unit_name: string;
  numerator: string;
  denominator: string;
  revision: number;
  state: string;
  created_by: string;
  reviewed_by: string | null;
}
const props = defineProps<{ sku: string; expanded?: boolean }>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
}>();
const data = ref<{
  sku_version: string;
  base_unit: string;
  items: Package[];
  has_more: boolean;
} | null>(null);
const offset = ref(0),
  loading = ref(false),
  creating = ref(false),
  selected = ref<Package | null>(null);
const form = ref({
  unit_name: "",
  numerator: "",
  denominator: "1",
  reason: "",
});
const review = ref({ decision: "APPROVE", evidence_ref: "", reason: "" });
const states: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "已启用",
  RETIRED: "已停用 / 被替代",
};
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(() => ({
    dirty: creating.value || !!selected.value,
    busy: busy.value,
  })),
);
watch([creating, selected, busy], () =>
  emit("editing", creating.value || !!selected.value || busy.value),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (reset) offset.value = 0;
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<typeof data.value>(
      `/api/v1/catalog/skus/${props.sku}/packaging?offset=${offset.value}`,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin() {
  creating.value = true;
  selected.value = null;
  form.value = { unit_name: "", numerator: "", denominator: "1", reason: "" };
}
function choose(row: Package) {
  selected.value = row;
  creating.value = false;
  review.value = {
    decision:
      row.state === "DRAFT" && row.created_by !== session.value?.principal_id
        ? "APPROVE"
        : "SUSPEND",
    evidence_ref: "",
    reason: "",
  };
}
async function create() {
  await execute(
    `/api/v1/catalog/skus/${props.sku}/packaging`,
    { expected_sku_version: data.value!.sku_version, ...form.value },
    async () => {
      creating.value = false;
      await load(true);
    },
  );
}
async function approve() {
  await execute(
    `/api/v1/catalog/skus/${props.sku}/packaging/${selected.value!.id}/review`,
    { expected_version: selected.value!.version, ...review.value },
    async () => {
      selected.value = null;
      await load(true);
    },
  );
}
onMounted(() => load());
</script>
<template>
  <details
    class="packaging-panel"
    data-testid="packaging-panel"
    :open="expanded"
  >
    <summary>其他包装与数量换算</summary>
    <div v-loading="loading" v-if="data">
      <p class="form-note">
        库存基本单位：{{
          data.base_unit
        }}。在这里添加包、箱等其他单位，并说明每件包装含多少{{
          data.base_unit
        }}。例如基本单位为盒：1箱＝100盒。
      </p>
      <div class="business-toolbar">
        <el-button
          v-if="can('catalog.write')"
          :disabled="busy || loading"
          @click="begin"
          >添加其他包装</el-button
        ><el-button :disabled="busy || loading" @click="load()"
          >刷新包装</el-button
        >
      </div>
      <el-table :data="data.items" row-key="id">
        <el-table-column label="单位 / 换算" min-width="155"
          ><template #default="{ row }"
            >{{ row.denominator }} {{ row.unit_name }} = {{ row.numerator }}
            {{ data.base_unit
            }}<small class="cell-sub"
              >包装：{{ row.unit_name }} · 第{{ row.revision }}版
            </small></template
          ></el-table-column
        >
        <el-table-column label="状态 / 审核人" min-width="170"
          ><template #default="{ row }"
            >{{ states[row.state]
            }}<small class="cell-sub">{{
              row.reviewed_by || "尚未批准"
            }}</small></template
          ></el-table-column
        >
        <el-table-column v-if="can('catalog.review')" label="操作" width="110"
          ><template #default="{ row }"
            ><el-button
              v-if="row.state !== 'RETIRED'"
              link
              type="primary"
              :disabled="busy || loading"
              @click="choose(row)"
              >审核包装</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <div class="pager">
        <el-button
          :disabled="!offset || busy || loading"
          @click="
            offset -= 50;
            load();
          "
          >上一页包装</el-button
        ><el-button
          :disabled="!data.has_more || busy || loading"
          @click="
            offset += 50;
            load();
          "
          >下一页包装</el-button
        >
      </div>
      <form v-if="creating" class="business-form" @submit.prevent="create">
        <h4>添加其他包装</h4>
        <label
          >包装名称<input
            v-model="form.unit_name"
            maxlength="30"
            required
            placeholder="例如包、箱、瓶"
            :disabled="busy"
        /></label>
        <div class="form-grid">
          <label
            >所含{{ data.base_unit }}数<input
              v-model="form.numerator"
              pattern="[1-9][0-9]{0,8}"
              maxlength="9"
              inputmode="numeric"
              required
              :disabled="busy" /></label
          ><label
            >对应包装件数<input
              v-model="form.denominator"
              pattern="[1-9][0-9]{0,8}"
              maxlength="9"
              inputmode="numeric"
              required
              :disabled="busy"
          /></label>
        </div>
        <p class="form-note">
          {{ form.denominator || "1" }} {{ form.unit_name || "件包装" }} ＝
          {{ form.numerator || "？" }} {{ data.base_unit }}。
          通常包装件数填1，例如1箱含100盒。若需按比例填写，也可以填2包含3个。
        </p>
        <label
          >包装维护原因<input
            v-model="form.reason"
            maxlength="200"
            required
            :disabled="busy"
        /></label>
        <div class="form-actions">
          <el-button :disabled="busy" @click="creating = false"
            >取消包装录入</el-button
          ><el-button native-type="submit" type="primary" :loading="busy"
            >保存待审包装</el-button
          >
        </div>
      </form>
      <form v-if="selected" class="business-form" @submit.prevent="approve">
        <h4>审核 {{ selected.unit_name }} · v{{ selected.revision }}</h4>
        <p class="form-note">
          核对实物包装与基本单位关系；建档人不能批准。新包装批准后可用于新单，历史单据保留原换算数量。
        </p>
        <label
          >包装审核结果<select v-model="review.decision" :disabled="busy">
            <option
              value="APPROVE"
              :disabled="
                selected.state !== 'DRAFT' ||
                selected.created_by === session?.principal_id
              "
            >
              审核通过
            </option>
            <option value="SUSPEND">停用 / 拒绝版本</option>
          </select></label
        >
        <label
          >包装核验凭据<input
            v-model="review.evidence_ref"
            maxlength="120"
            required
            :disabled="busy"
        /></label>
        <label
          >包装审核说明<input
            v-model="review.reason"
            maxlength="200"
            required
            :disabled="busy"
        /></label>
        <div class="form-actions">
          <el-button :disabled="busy" @click="selected = null"
            >取消包装审核</el-button
          ><el-button native-type="submit" type="primary" :loading="busy"
            >保存包装审核</el-button
          >
        </div>
      </form>
    </div>
  </details>
</template>
<style scoped>
.packaging-panel {
  margin: 20px 0;
  border-top: 1px solid #e5eceb;
  padding-top: 16px;
}
summary {
  cursor: pointer;
  font-weight: 600;
}
</style>
