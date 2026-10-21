<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { registerCatalogChanges } from "./catalogChanges";
import { request } from "./api";
import { can, useCommand, type PageData } from "./business";
import { localTime } from "./sales";
import type { CatalogEvent } from "./CatalogHistory.vue";
const props = defineProps<{
  kind: "skus" | "parties";
  target: string;
  category?: string;
  title?: string;
  schemes?: string[];
  baseUnit?: string;
  readonly?: boolean;
}>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
}>();
interface Mapping {
  id: string;
  version: string;
  scheme: string;
  namespace: string;
  external_code: string;
  evidence_ref: string;
  state: string;
  unit_name: string | null;
  packaging_state: string | null;
}
interface Package {
  id: string;
  unit_name: string;
  state: string;
  revision: number;
  numerator: string;
  denominator: string;
}
const barcodeOnly = computed(
  () => props.schemes?.length === 1 && props.schemes[0] === "BARCODE",
);
const baseUnitLabel = computed(() =>
  props.baseUnit ? `基本单位（${props.baseUnit}）` : "商品基本单位",
);
const schemes: Record<string, string> = {
  BARCODE: "商品条码",
  UDI_DI: "UDI 产品标识（DI）",
  NHSA_DRUG: "医保药品编码",
  NHSA_CONSUMABLE: "医保耗材编码",
  NHSA_IVD: "医保体外诊断试剂编码",
  NHSA_ORGANIZATION: "医保机构编码",
  MSFX: "码上放心",
  MEITUAN: "美团",
  ELEME: "饿了么",
  TAOBAO: "淘宝",
  WANGDIANTONG: "旺店通",
  CUSTOM: "其他系统",
};
const standard = new Set([
  "BARCODE",
  "UDI_DI",
  "NHSA_DRUG",
  "NHSA_CONSUMABLE",
  "NHSA_IVD",
  "NHSA_ORGANIZATION",
]);
const available = computed(() =>
  Object.entries(schemes)
    .filter(([key]) => !props.schemes || props.schemes.includes(key))
    .filter(
      ([key]) =>
        !standard.has(key) ||
        (props.kind === "parties"
          ? key === "NHSA_ORGANIZATION"
          : key === "BARCODE" ||
            (props.category === "DRUG"
              ? key === "NHSA_DRUG"
              : ["UDI_DI", "NHSA_CONSUMABLE", "NHSA_IVD"].includes(key))),
    ),
);
const base = computed(
  () => `/api/v1/catalog/${props.kind}/${props.target}/codes`,
);
const data = ref<PageData<Mapping> & { history: CatalogEvent[] }>({
  items: [],
  has_more: false,
  history: [],
});
const packages = ref<Package[]>([]),
  packageOffset = ref(0),
  packageMore = ref(false);
const loading = ref(false),
  packageLoading = ref(false),
  offset = ref(0),
  adding = ref(false),
  selected = ref<Mapping | null>(null),
  reason = ref("");
const fresh = () => ({
  scheme: available.value[0]?.[0] || "CUSTOM",
  namespace: "",
  external_code: "",
  packaging_id: "",
  evidence_ref: barcodeOnly.value ? "商品条码直接录入" : "",
  reason: barcodeOnly.value ? "维护商品条码" : "",
});
const form = ref(fresh());
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(
    () => ({
      dirty: adding.value || !!selected.value,
      busy: busy.value,
    }),
    props.kind === "parties" ? "parties" : "products",
  ),
);
watch([adding, selected, busy], () =>
  emit("editing", adding.value || !!selected.value || busy.value),
);
let epoch = 0,
  packageEpoch = 0;
onUnmounted(() => {
  epoch++;
  packageEpoch++;
});
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const query = new URLSearchParams({ offset: String(offset.value) });
    props.schemes?.forEach((s) => query.append("schemes", s));
    const result = await request<typeof data.value>(`${base.value}?${query}`);
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function loadPackages(reset = true) {
  if (props.kind !== "skus") return;
  const current = ++packageEpoch;
  packageLoading.value = true;
  if (reset) {
    packages.value = [];
    packageOffset.value = 0;
    packageMore.value = false;
  }
  try {
    const result = await request<PageData<Package>>(
      `/api/v1/catalog/skus/${props.target}/packaging?offset=${packageOffset.value}`,
    );
    if (current !== packageEpoch) return;
    packages.value.push(...result.items.filter((p) => p.state === "APPROVED"));
    packageMore.value = result.has_more;
    packageOffset.value += result.items.length;
  } catch (e) {
    if (current === packageEpoch) emit("failure", e);
  } finally {
    if (current === packageEpoch) packageLoading.value = false;
  }
}
function begin() {
  form.value = fresh();
  adding.value = true;
  selected.value = null;
  void loadPackages();
}
async function create() {
  await execute(
    base.value,
    {
      ...form.value,
      namespace: standard.has(form.value.scheme) ? "" : form.value.namespace,
      packaging_id: form.value.packaging_id || null,
    },
    async () => {
      adding.value = false;
      offset.value = 0;
      await load();
    },
  );
}
async function changeState() {
  if (!selected.value) return;
  await execute(
    `${base.value}/${selected.value.id}/state`,
    {
      expected_version: selected.value.version,
      state: selected.value.state === "ACTIVE" ? "INACTIVE" : "ACTIVE",
      reason: reason.value,
    },
    async () => {
      selected.value = null;
      await load();
    },
  );
}
onMounted(load);
</script>
<template>
  <section class="catalog-code-panel" aria-label="对接编码" v-loading="loading">
    <div class="business-toolbar">
      <h3>
        {{ barcodeOnly ? "商品条码" : "对接编码"
        }}<span v-if="title"> · {{ title }}</span>
      </h3>
      <el-button
        v-if="!readonly && can('catalog.write')"
        :disabled="busy || adding || !!selected"
        @click="begin"
        >新增编码</el-button
      ><el-button v-if="!readonly" :disabled="busy" @click="load"
        >刷新编码</el-button
      >
    </div>
    <p v-if="barcodeOnly" class="form-note">
      每条条码对应一个计量单位。例如盒码选择基本单位，箱码选择已启用的“箱”；条码前面的0会保留。
    </p>
    <p v-else class="form-note">
      编码按原文保存，保留前导零；平台编码需区分店铺或账号。UDI 此处仅登记
      DI，批号等生产标识随单据管理。编码登记不代表平台已验证或已接通。
    </p>
    <p v-if="props.schemes?.includes('MSFX')" class="form-note">
      码上放心卡片登记平台商品或单位编码；每盒药品的追溯码随出入库单据采集，不填入此处。
    </p>
    <el-table :data="data.items" row-key="id" empty-text="尚未登记对接编码">
      <el-table-column label="编码类型 / 范围" min-width="180"
        ><template #default="{ row }"
          >{{ schemes[row.scheme]
          }}<small class="cell-sub">{{
            row.namespace || "标准编码"
          }}</small></template
        ></el-table-column
      >
      <el-table-column
        prop="external_code"
        :label="barcodeOnly ? '条码' : '外部编码'"
        min-width="200"
      />
      <el-table-column label="条码对应单位 / 编码对象" min-width="140"
        ><template #default="{ row }"
          >{{ row.unit_name || (kind === "skus" ? baseUnitLabel : "单位档案")
          }}<small
            v-if="row.packaging_state && row.packaging_state !== 'APPROVED'"
            class="cell-sub"
            >原包装已停用，请核对编码</small
          ></template
        ></el-table-column
      >
      <el-table-column prop="evidence_ref" label="来源凭据" min-width="140" />
      <el-table-column label="状态" width="90"
        ><template #default="{ row }">{{
          row.state === "ACTIVE" ? "启用" : "停用"
        }}</template></el-table-column
      >
      <el-table-column
        v-if="!readonly && can('catalog.write')"
        label="操作"
        width="85"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :disabled="busy || adding || !!selected"
            @click="
              selected = row;
              reason = '';
            "
            >{{ row.state === "ACTIVE" ? "停用" : "启用" }}</el-button
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
        >上一页编码</el-button
      ><el-button
        :disabled="!data.has_more || busy || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页编码</el-button
      >
    </div>
    <form
      v-if="adding"
      class="business-form code-form"
      aria-label="新增对接编码"
      @submit.prevent="create"
    >
      <fieldset :disabled="busy" class="form-grid">
        <label v-if="!barcodeOnly"
          >编码类型<select v-model="form.scheme">
            <option v-for="[key, name] in available" :key="key" :value="key">
              {{ name }}
            </option>
          </select></label
        >
        <label v-if="!standard.has(form.scheme)"
          >店铺 / 账号 / 系统标识<input
            v-model="form.namespace"
            required
            maxlength="80"
            placeholder="同一平台内用于区分编码范围"
        /></label>
        <label
          >{{ barcodeOnly ? "条码" : "外部编码"
          }}<input
            v-model="form.external_code"
            required
            maxlength="128"
            placeholder="按来源原文填写，不含空白字符"
        /></label>
        <label v-if="kind === 'skus'"
          >{{ barcodeOnly ? "条码对应单位" : "编码对应单位"
          }}<select v-model="form.packaging_id">
            <option value="">{{ baseUnitLabel }}</option>
            <option v-for="p in packages" :key="p.id" :value="p.id">
              {{ p.unit_name }}（1{{ p.unit_name }}＝{{ p.numerator
              }}{{ p.denominator === "1" ? "" : "/" + p.denominator
              }}{{ baseUnit || "基本单位" }}）
            </option></select
          ><el-button
            v-if="packageMore"
            :loading="packageLoading"
            @click="loadPackages(false)"
            >加载更多包装</el-button
          ></label
        >
        <label
          >编码来源凭据<input
            v-model="form.evidence_ref"
            required
            maxlength="120"
            placeholder="标签、平台资料或核验档案编号"
        /></label>
        <label
          >登记原因<input v-model="form.reason" required maxlength="200"
        /></label>
      </fieldset>
      <div class="form-actions">
        <el-button :disabled="busy" @click="adding = false"
          >取消新增编码</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >保存编码</el-button
        >
      </div>
    </form>
    <form
      v-if="selected"
      class="business-form code-form"
      aria-label="调整编码状态"
      @submit.prevent="changeState"
    >
      <p>
        {{ selected.state === "ACTIVE" ? "停用" : "重新启用" }}：{{
          selected.external_code
        }}。记录保留；填写错误的编码请停用后重新登记。
      </p>
      <label
        >状态调整原因<input
          v-model="reason"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <div class="form-actions">
        <el-button :disabled="busy" @click="selected = null">取消调整</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >确认调整编码</el-button
        >
      </div>
    </form>
    <details v-if="data.history.length">
      <summary>最近编码变更（最多 50 条）</summary>
      <p v-for="e in data.history" :key="e.id">
        {{ localTime(e.created_at) }} ·
        {{
          e.action === "catalog_code.created"
            ? "登记编码"
            : e.detail.state === "ACTIVE"
              ? "启用编码"
              : "停用编码"
        }}
        · {{ e.detail.external_code }} · {{ e.detail.reason
        }}<small class="cell-sub"
          >操作人：{{ e.created_by
          }}<span v-if="e.detail.evidence_ref">
            · 来源：{{ e.detail.evidence_ref }}</span
          ></small
        >
      </p>
    </details>
  </section>
</template>
<style scoped>
.catalog-code-panel {
  margin: 20px 0;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
}
h3 {
  margin: 0 auto 0 0;
  font-size: 16px;
}
h3 span {
  font-size: 13px;
  font-weight: normal;
}
fieldset {
  border: 0;
  margin: 0;
  padding: 0;
  min-width: 0;
}
.code-form {
  padding: 16px;
  background: var(--bg, #f4f7fa);
  border-radius: 6px;
}
summary {
  cursor: pointer;
}
details {
  margin-top: 12px;
}
details p {
  overflow-wrap: anywhere;
}
</style>
