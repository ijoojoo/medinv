<script setup lang="ts">
import DateInput from "./DateInput.vue";
import InformationCard from "./InformationCard.vue";
import { activeEntity } from "./entityScope";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import CatalogCodes from "./CatalogCodes.vue";
import CatalogOperations from "./CatalogOperations.vue";
import CatalogPrices from "./CatalogPrices.vue";
import ProductCertificates from "./ProductCertificates.vue";
import PartyCertificates from "./PartyCertificates.vue";
import ProductImport from "./ProductImport.vue";
import DictionaryValue from "./DictionaryValue.vue";
import PartyProfileFields from "./PartyProfileFields.vue";
import PartyOverview from "./PartyOverview.vue";
import CatalogColumnSettings from "./CatalogColumnSettings.vue";
import { partyListColumns, useCatalogColumns } from "./catalogColumns";
import PartyIdentityFields from "./PartyIdentityFields.vue";
import {
  organizationTypes,
  readPartyProfile,
  type PartyProfile,
} from "./partyProfile";
import { guardCatalogLeave, registerCatalogChanges } from "./catalogChanges";
import { submitCatalogForm, useCatalogListReturn } from "./catalogInteraction";
import { confirmDates } from "./dateControls";
import PackagingPanel from "./PackagingPanel.vue";
import ProductFields from "./ProductFields.vue";
import {
  productCoreFields,
  credentialLabel,
  effectiveFields,
  type ProductFieldPresentation,
  type ProductFieldPolicy,
} from "./productFieldRules";
import ProductProfileDetails from "./ProductProfileDetails.vue";
import {
  emptyProductProfile,
  readProductProfile,
  profileForCategory,
  type ProductProfile,
} from "./productProfile";
import CatalogHistory, { type CatalogEvent } from "./CatalogHistory.vue";
import { request, session } from "./api";
import { can, labels, useCommand, type PageData } from "./business";
const props = defineProps<{ kind: "products" | "parties" }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
interface RecordData extends Partial<ProductProfile>, Partial<PartyProfile> {
  id: string;
  name: string;
  code: string;
  version: string;
  created_by: string;
  revision_editors: string[];
  skus?: { id: string; code: string; base_unit: string; qty_scale: number }[];
  approval_state?: string;
  state?: string;
  category?: string;
  drug_sale_class?: string;
  categories?: string[];
  manufacturer_name?: string;
  specification?: string;
  approval_number?: string;
  sku_code?: string;
  base_unit?: string;
  qty_scale?: number;
  credit_code?: string;
  license_no?: string;
  kind?: string;
  organization?: { id: string; name: string; legal_code: string };
  valid_from: string;
  valid_until: string;
}
const drugClasses: Record<string, string> = {
  UNKNOWN: "待核定",
  OTC: "非处方药 OTC",
  RX: "处方药 RX",
};
const product = computed(() => props.kind === "products");
const infoOpen = ref(false);
const partyRoles = [
  { key: "", name: "全部往来单位", icon: "▧" },
  { key: "SUPPLIER", name: "供应商", icon: "↓" },
  { key: "CUSTOMER", name: "客户", icon: "↑" },
  { key: "BOTH", name: "供销双向单位", icon: "⇄" },
];
async function chooseRole(key: string) {
  if (!(await guardCatalogLeave(props.kind))) return;
  creating.value = false;
  selected.value = null;
  editing.value = null;
  roleFilter.value = key;
  await load(true);
}
const columnSettings = ref(false);
const partyColumns = useCatalogColumns(
  ref("parties"),
  computed(() => partyListColumns),
);
function partyCell(row: RecordData, key: string) {
  if (key === "kind") return labels[row.kind || ""] || row.kind || "未填写";
  if (key === "organization_type")
    return (
      organizationTypes[row.organization_type || "UNKNOWN"] ||
      row.organization_type
    );
  const value = (row as unknown as Record<string, unknown>)[key];
  return value == null || value === "" ? "未填写" : String(value);
}
const endpoint = computed(() => `/api/v1/catalog/${props.kind}`);
const entity = ref(activeEntity.value);
const search = ref(""),
  offset = ref(0),
  loading = ref(false),
  creating = ref(false);
const categoryFilter = ref(""),
  roleFilter = ref(""),
  stateFilter = ref("");
const importing = ref(false),
  importVisited = ref(false);
const applied = ref({ search: "", category: "", role: "", state: "" });
const baseline = ref("");
const formTitle = computed(() =>
  editing.value ? "修订资料" : product.value ? "新建商品档案" : "新建往来单位",
);
const data = ref<PageData<RecordData>>({ items: [], has_more: false });
const selected = ref<RecordData | null>(null);
const operationsCard = ref<InstanceType<typeof CatalogOperations> | null>(null);
async function maintainParty(field: string) {
  selectTab("operations");
  await nextTick();
  operationsCard.value?.focusField(field);
}
const detailPolicy = ref<ProductFieldPresentation | null>(null);
function detailLabel(key: string, fallback: string) {
  const values = Object.fromEntries(
    Object.entries(selected.value || {}).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string",
    ),
  );
  return (
    effectiveFields(detailPolicy.value?.fields || [], values).find(
      (f) => f.key === key,
    )?.label || fallback
  );
}
const activeTab = ref("basic"),
  visitedTabs = ref(["basic"]);
const tabs = computed(() => [
  { id: "basic", name: "基本信息", schemes: [] },
  { id: "operations", name: "经营信息", schemes: [] },
  { id: "quality", name: "质量管控", schemes: [] },
  ...(product.value ? [{ id: "prices", name: "价格信息", schemes: [] }] : []),
  {
    id: "insurance",
    name: "医保信息",
    schemes: product.value
      ? selected.value?.category === "DRUG"
        ? ["NHSA_DRUG"]
        : ["NHSA_CONSUMABLE", "NHSA_IVD"]
      : ["NHSA_ORGANIZATION"],
  },
  {
    id: "trace",
    name: "追溯信息",
    schemes:
      product.value && selected.value?.category === "DEVICE"
        ? ["MSFX", "UDI_DI"]
        : ["MSFX"],
  },
  {
    id: "platforms",
    name: "第三方对接",
    schemes: ["MEITUAN", "ELEME", "TAOBAO", "WANGDIANTONG", "CUSTOM"],
  },
  { id: "history", name: "变更记录", schemes: [] },
]);
function selectTab(id: string) {
  activeTab.value = id;
  if (!visitedTabs.value.includes(id)) visitedTabs.value.push(id);
}
function focusTab(id: string) {
  selectTab(id);
  document.getElementById(`${props.kind}-tab-${id}`)?.focus();
}
function moveTab(direction: number) {
  const index = tabs.value.findIndex((t) => t.id === activeTab.value);
  focusTab(
    tabs.value[(index + direction + tabs.value.length) % tabs.value.length].id,
  );
}
watch(
  () => selected.value?.id,
  () => {
    activeTab.value =
      can("catalog.review") && !can("catalog.write") ? "quality" : "basic";
    visitedTabs.value = ["basic", activeTab.value];
    codeEditing.value = {};
  },
);
const editing = ref<RecordData | null>(null),
  revisionReason = ref("");
const history = ref<CatalogEvent[]>([]),
  historyOffset = ref(0),
  historyMore = ref(false),
  detailLoading = ref(false);
const listRoot = ref<HTMLElement>();
const listReturn = useCatalogListReturn(listRoot);
let detailTabBeforeEdit = "basic";
const cannotApprove = computed(
  () =>
    selected.value?.created_by === session.value?.principal_id ||
    selected.value?.revision_editors.includes(
      session.value?.principal_id || "",
    ),
);
const categoryNames = computed(() =>
  selected.value?.categories?.map((c) => labels[c]).join("、"),
);
const review = ref({ decision: "APPROVE", evidence_ref: "", reason: "" });
const fresh = () => ({
  profile: emptyProductProfile(),
  partyProfile: readPartyProfile(),
  code: "",
  name: "",
  category: "DEVICE",
  drug_sale_class: "UNKNOWN",
  manufacturer_name: "",
  specification: "",
  approval_number: "",
  valid_from: "",
  valid_until: "",
  sku_code: "",
  base_unit: "个",
  qty_scale: 0,
  credit_code: "",
  kind: "SUPPLIER",
  license_no: "",
  categories: ["DEVICE"],
});
const form = ref(fresh());
const organizationId = ref("");
const entryTab = ref("basic");
const entryForm = ref<HTMLFormElement>();
const entryTabs = computed(() => [
  { id: "basic", name: "基本信息" },
  { id: "quality", name: "质量管控" },
  ...(!editing.value ? [{ id: "operations", name: "联系信息" }] : []),
]);
function moveEntryTab(direction: number) {
  const i = entryTabs.value.findIndex((t) => t.id === entryTab.value);
  entryTab.value =
    entryTabs.value[
      (i + direction + entryTabs.value.length) % entryTabs.value.length
    ].id;
  void nextTick(() =>
    document.getElementById(`party-entry-${entryTab.value}`)?.focus(),
  );
}
async function openExistingParty(id: string) {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        "打开已有单位将放弃本次未保存的建档内容。",
        "使用已有资料",
        { confirmButtonText: "打开已有资料", cancelButtonText: "继续建档" },
      );
    } catch {
      return;
    }
  }
  await detail({ id } as RecordData);
  if (selected.value?.id === id) creating.value = false;
}
const signature = () => JSON.stringify([form.value, revisionReason.value]);
const dirty = computed(() => creating.value && signature() !== baseline.value);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    props.kind,
  ),
);
const codeEditing = ref<Record<string, boolean>>({});
const hasCodeEdits = computed(() =>
  Object.values(codeEditing.value).some(Boolean),
);
async function cancelForm() {
  if (busy.value) return;
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        "本次修改尚未保存，确认放弃？",
        "离开资料编辑",
        {
          confirmButtonText: "放弃修改",
          cancelButtonText: "继续编辑",
          type: "warning",
        },
      );
    } catch {
      return;
    }
  }
  creating.value = false;
  if (editing.value) await detail(editing.value);
  else await listReturn.restore();
  editing.value = null;
}
async function backToList() {
  if (detailLoading.value || !(await guardCatalogLeave(props.kind))) return;
  // A card's save can start a detail refresh while the leave guard is waiting.
  // Once the user returns, that response must not reopen the previous record.
  detailEpoch++;
  detailLoading.value = false;
  selected.value = null;
  await load();
  await listReturn.restore();
}
function resetQuery() {
  search.value = "";
  categoryFilter.value = "";
  roleFilter.value = "";
  stateFilter.value = "";
  void load(true);
}
const fieldPolicy = ref<ProductFieldPolicy | null>(null);
const basicEntryFields = computed(() =>
  (fieldPolicy.value?.fields || []).filter((f) => f.card === "basic"),
);
const qualityEntryFields = computed(() =>
  (fieldPolicy.value?.fields || []).filter((f) => f.card === "quality"),
);
const operationEntryFields = computed(() =>
  (fieldPolicy.value?.fields || []).filter((f) => f.card === "operations"),
);
const entryTemplateName = computed(() => {
  const template = fieldPolicy.value?.template;
  return (
    template?.variants[form.value.profile.device_class]?.name || template?.name
  );
});
const hasLegacyCodes = computed(() =>
  selected.value?.skus?.some((s) => s.code !== selected.value?.code),
);
const rulesLoading = ref(false);
let rulesEpoch = 0;
const productValues = computed<Record<string, string>>({
  get: () => ({
    ...form.value.profile,
    ...Object.fromEntries(productCoreFields.map((k) => [k, form.value[k]])),
  }),
  set: (values) => {
    for (const key of productCoreFields) form.value[key] = values[key] ?? "";
    for (const key of Object.keys(
      form.value.profile,
    ) as (keyof ProductProfile)[])
      form.value.profile[key] = values[key] ?? form.value.profile[key];
  },
});
async function loadFieldPolicy() {
  const current = ++rulesEpoch;
  fieldPolicy.value = null;
  if (!creating.value || !product.value) return;
  rulesLoading.value = true;
  try {
    const result = await request<ProductFieldPolicy>(
      `/api/v1/catalog/field-rules/${form.value.category}`,
    );
    if (current === rulesEpoch) fieldPolicy.value = result;
  } catch (error) {
    if (current === rulesEpoch) emit("failure", error);
  } finally {
    if (current === rulesEpoch) rulesLoading.value = false;
  }
}
watch([creating, () => form.value.category], () => {
  void loadFieldPolicy();
});
const { busy, execute } = useCommand((error) => emit("failure", error));
let epoch = 0;
let detailEpoch = 0;
onUnmounted(() => {
  epoch++;
  detailEpoch++;
  rulesEpoch++;
});
async function load(reset = false) {
  if (reset) {
    listReturn.clear();
    offset.value = 0;
    applied.value = {
      search: search.value.trim(),
      category: categoryFilter.value,
      role: roleFilter.value,
      state: stateFilter.value,
    };
  }
  if (!product.value && !entity.value) return;
  const current = ++epoch;
  loading.value = true;
  data.value = { items: [], has_more: false };
  try {
    const result = await request<PageData<RecordData>>(
      `${endpoint.value}?${new URLSearchParams({
        search: applied.value.search,
        state: applied.value.state,
        ...(product.value
          ? { category: applied.value.category }
          : { role: applied.value.role }),
        offset: String(offset.value),
        ...(!product.value ? { entity_id: entity.value } : {}),
      })}`,
    );
    if (current === epoch) {
      if (offset.value && !result.items.length) {
        offset.value = Math.max(0, offset.value - 50);
        await load();
        return;
      }
      data.value = result;
    }
  } catch (error) {
    if (current === epoch) emit("failure", error);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function newRecord() {
  listReturn.capture();
  editing.value = null;
  form.value = fresh();
  organizationId.value = "";
  entryTab.value = "basic";
  if (!product.value && roleFilter.value) form.value.kind = roleFilter.value;
  revisionReason.value = "";
  baseline.value = signature();
  creating.value = true;
}
async function detail(row: RecordData, reset = true, fromList = false) {
  if (fromList) listReturn.capture(row.id);
  if (reset) historyOffset.value = 0;
  const current = ++detailEpoch;
  detailLoading.value = true;
  try {
    const result = await request<{
      record: RecordData;
      field_policy: ProductFieldPresentation | null;
      history: CatalogEvent[];
      has_more: boolean;
    }>(`${endpoint.value}/${row.id}?offset=${historyOffset.value}`);
    if (current !== detailEpoch) return;
    detailPolicy.value = result.field_policy;
    const sku =
      result.record.skus?.find((s) => s.code === row.sku_code) ||
      result.record.skus?.[0];
    selected.value = {
      ...result.record,
      ...(sku
        ? {
            sku_code: sku.code,
            base_unit: sku.base_unit,
            qty_scale: sku.qty_scale,
          }
        : {}),
    };
    history.value = result.history;
    historyMore.value = result.has_more;
    review.value = { decision: "APPROVE", evidence_ref: "", reason: "" };
  } catch (error) {
    if (current === detailEpoch) emit("failure", error);
  } finally {
    if (current === detailEpoch) detailLoading.value = false;
  }
}
function revise() {
  if (!selected.value) return;
  detailTabBeforeEdit = activeTab.value;
  editing.value = selected.value;
  form.value = {
    ...fresh(),
    ...Object.fromEntries(
      Object.entries(selected.value).filter(([, v]) => v != null),
    ),
    profile: readProductProfile(selected.value),
    partyProfile: readPartyProfile(selected.value),
  };
  revisionReason.value = "";
  entryTab.value = "basic";
  baseline.value = signature();
  selected.value = null;
  creating.value = true;
}
async function create(returnToList = false) {
  if (busy.value || !can("catalog.write") || !confirmDates(entryForm.value))
    return;
  const invalid = entryForm.value?.querySelector<HTMLInputElement>(
    "input:invalid,select:invalid,textarea:invalid",
  );
  if (invalid) {
    const tab =
      invalid.closest<HTMLElement>("[data-entry-tab]")?.dataset.entryTab;
    if (tab) entryTab.value = tab;
    await nextTick();
    invalid.focus();
    invalid.reportValidity();
    return;
  }
  if (product.value && (!fieldPolicy.value || rulesLoading.value)) return;
  let returned = false;
  const f = form.value;
  if (editing.value) {
    const original = editing.value;
    await execute(
      `${endpoint.value}/${original.id}/revise`,
      {
        expected_version: original.version,
        name: f.name,
        valid_from: f.valid_from,
        valid_until: f.valid_until,
        reason: revisionReason.value,
        ...(product.value
          ? {
              ...profileForCategory(f.profile, f.category),
              manufacturer_name: f.manufacturer_name,
              specification: f.specification,
              approval_number: f.approval_number,
              drug_sale_class:
                f.category === "DRUG" ? f.drug_sale_class : "UNKNOWN",
            }
          : {
              ...f.partyProfile,
              kind: f.kind,
              license_no: f.license_no,
              categories: f.categories,
            }),
      },
      async () => {
        creating.value = false;
        editing.value = null;
        await load();
        if (returnToList) returned = true;
        else {
          await detail(original);
          await nextTick();
          selectTab(detailTabBeforeEdit);
        }
      },
    );
    if (returned) await listReturn.restore();
    return;
  }
  const common = {
    code: f.code,
    name: f.name,
    valid_from: f.valid_from,
    valid_until: f.valid_until,
  };
  const body = product.value
    ? {
        ...common,
        ...profileForCategory(f.profile, f.category),
        category: f.category,
        drug_sale_class: f.category === "DRUG" ? f.drug_sale_class : "UNKNOWN",
        manufacturer_name: f.manufacturer_name,
        specification: f.specification,
        approval_number: f.approval_number,
        code: f.code.trim() || undefined,
        base_unit: f.base_unit,
        qty_scale: f.qty_scale,
      }
    : {
        ...common,
        ...f.partyProfile,
        organization: organizationId.value ? { id: organizationId.value } : {},
        entity_id: entity.value,
        credit_code: f.credit_code,
        kind: f.kind,
        license_no: f.license_no,
        categories: f.categories,
      };
  await execute(endpoint.value, body, async () => {
    creating.value = false;
    await load();
    returned = true;
  });
  if (returned) await listReturn.restore();
}
async function saveReview() {
  if (!selected.value) return;
  let returned = false;
  await execute(
    `${endpoint.value}/${selected.value.id}/review`,
    {
      expected_version: selected.value.version,
      ...review.value,
    },
    async () => {
      selected.value = null;
      await load();
      returned = true;
    },
  );
  if (returned) await listReturn.restore();
}
onMounted(() => load());
</script>
<template>
  <div :class="{ 'master-workspace party-workspace': !product }">
    <aside v-if="!product" class="master-sidebar party-sidebar">
      <div class="master-side-heading">往来单位 <span>业务身份</span></div>
      <nav aria-label="往来单位分类">
        <button
          v-for="r in partyRoles"
          :key="r.key"
          :class="{ active: roleFilter === r.key }"
          :disabled="busy || detailLoading"
          @click="chooseRole(r.key)"
        >
          <span>{{ r.icon }}</span
          >{{ r.name }}
        </button>
      </nav>
      <p class="master-side-note">
        一个单位可同时作为供应商和客户。共享机构身份，按当前经营主体维护经营资料与准入资质。
      </p>
    </aside>
    <div :class="{ 'master-main': !product }">
      <div v-show="importing">
        <ProductImport
          v-if="product && importVisited"
          @failure="emit('failure', $event)"
          @close="importing = false"
          @saved="load(true)"
        />
      </div>
      <section
        v-show="!creating && !selected && !importing"
        ref="listRoot"
        class="panel catalog-list"
        aria-label="基础资料列表"
        v-loading="loading"
      >
        <div class="catalog-heading">
          <h2>{{ product ? "商品资料" : "往来单位" }}</h2>
          <span class="form-note">统一建档 · 独立审核 · 编码映射</span>
        </div>
        <form class="business-toolbar" @submit.prevent="load(true)">
          <label
            >查询资料<input
              v-model="search"
              data-catalog-search
              @keydown="listReturn.keys"
              :disabled="detailLoading"
              maxlength="200"
              :placeholder="
                product
                  ? '名称、别名、内部或外部编码、厂家、批准编号'
                  : '名称、简称、助记码、信用代码、联系人或对接编码'
              "
              aria-label="查询资料"
          /></label>
          <label v-if="product"
            >商品类别<select
              v-model="categoryFilter"
              :disabled="detailLoading"
              @change="load(true)"
            >
              <option value="">全部类别</option>
              <option value="DRUG">药品</option>
              <option value="DEVICE">医疗器械</option>
            </select></label
          >
          <label v-else
            >业务身份<select
              v-model="roleFilter"
              :disabled="detailLoading"
              @change="load(true)"
            >
              <option value="">全部单位</option>
              <option value="SUPPLIER">供应商（含双向单位）</option>
              <option value="CUSTOMER">客户（含双向单位）</option>
              <option value="BOTH">仅供应商及客户</option>
            </select></label
          >
          <label
            >资料状态<select
              v-model="stateFilter"
              :disabled="detailLoading"
              @change="load(true)"
            >
              <option value="">全部状态</option>
              <option value="DRAFT">待审核</option>
              <option value="APPROVED">已审核</option>
              <option value="SUSPENDED">已停用</option>
            </select></label
          >
          <el-button native-type="submit" :disabled="detailLoading"
            >查询</el-button
          >
          <el-button :disabled="loading || detailLoading" @click="resetQuery"
            >重置</el-button
          >
          <el-button
            v-if="!product"
            :disabled="loading || detailLoading"
            @click="columnSettings = true"
            >列设置</el-button
          >
          <el-button
            v-if="can('catalog.write')"
            type="primary"
            :disabled="busy || detailLoading || (!product && !entity)"
            @click="newRecord"
            >{{ product ? "新建商品" : "新建往来单位" }}</el-button
          >
          <el-button
            v-if="product && can('catalog.write')"
            :disabled="busy || detailLoading"
            @click="
              importVisited = true;
              importing = true;
            "
            >批量导入商品</el-button
          >
        </form>
        <el-table
          v-if="!product"
          :key="
            partyColumns.visible.value
              .map((c) => `${c.key}:${c.width}:${c.wrap}`)
              .join(',')
          "
          class="party-configurable-list"
          :data="data.items"
          :row-class-name="
            ({ row }: { row: RecordData }) =>
              row.id === listReturn.returnedId.value
                ? 'catalog-returned-row'
                : ''
          "
          empty-text="暂无资料，请先建档，再由另一位成员审核"
        >
          <el-table-column
            v-for="column in partyColumns.visible.value"
            :key="column.key"
            :column-key="column.key"
            :label="column.label"
            :width="column.width"
            :class-name="column.wrap ? 'party-cell-wrap' : 'party-cell-clip'"
            :show-overflow-tooltip="!column.wrap"
          >
            <template #default="{ row }">
              <template v-if="column.key === 'identity'"
                ><b>{{ row.name }}</b
                ><small class="cell-sub">{{ row.code }}</small></template
              >
              <template v-else-if="column.key === 'contact'"
                >{{
                  [row.contact_name, row.contact_phone]
                    .filter(Boolean)
                    .join(" · ") || "未填写联系人"
                }}<small class="cell-sub">{{
                  row.credit_code
                }}</small></template
              >
              <template v-else-if="column.key === 'validity'"
                >{{ row.valid_from || "待补齐"
                }}<small class="cell-sub"
                  >至 {{ row.valid_until || "待补齐" }}</small
                ></template
              >
              <el-tag
                v-else-if="column.key === 'state'"
                :type="row.state === 'APPROVED' ? 'success' : 'info'"
                >{{ labels[row.state] }}</el-tag
              >
              <template v-else>{{ partyCell(row, column.key) }}</template>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" fixed="right"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                :disabled="detailLoading || busy"
                :data-catalog-record="row.id"
                @keydown="listReturn.keys"
                @click="detail(row, true, true)"
                >资料详情</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <el-table
          v-else
          :data="data.items"
          :row-class-name="
            ({ row }: { row: RecordData }) =>
              row.id === listReturn.returnedId.value
                ? 'catalog-returned-row'
                : ''
          "
          empty-text="暂无资料，请先建档，再由另一位成员审核"
        >
          <el-table-column label="名称 / 编码" min-width="210"
            ><template #default="{ row }"
              ><b>{{ row.name }}</b
              ><small class="cell-sub">{{ row.code }}</small></template
            ></el-table-column
          >
          <el-table-column
            :label="product ? '类别 / 规格' : '单位类型'"
            min-width="140"
            ><template #default="{ row }"
              >{{ labels[product ? row.category : row.kind]
              }}<small v-if="product" class="cell-sub">{{
                row.specification || "待补齐"
              }}</small></template
            ></el-table-column
          >
          <el-table-column
            :label="product ? '生产企业 / 资质编号' : '联系 / 信用代码'"
            min-width="200"
            ><template #default="{ row }"
              >{{
                product
                  ? row.manufacturer_name
                  : [row.contact_name, row.contact_phone]
                      .filter(Boolean)
                      .join(" · ") || "未填写联系人"
              }}<small class="cell-sub">{{
                product ? row.approval_number : row.credit_code
              }}</small></template
            ></el-table-column
          >
          <el-table-column label="资质有效期" min-width="195"
            ><template #default="{ row }"
              >{{ row.valid_from || "待补齐"
              }}<small class="cell-sub"
                >至 {{ row.valid_until || "待补齐" }}</small
              ></template
            ></el-table-column
          >
          <el-table-column label="状态" width="105"
            ><template #default="{ row }"
              ><el-tag
                :type="
                  (row.approval_state || row.state) === 'APPROVED'
                    ? 'success'
                    : 'info'
                "
                >{{ labels[row.approval_state || row.state] }}</el-tag
              ></template
            ></el-table-column
          >
          <el-table-column label="操作" width="110"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                :disabled="detailLoading || busy"
                :data-catalog-record="row.id"
                @keydown="listReturn.keys"
                @click="detail(row, true, true)"
                >资料详情</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <div class="pager">
          <span
            >第 {{ Math.floor(offset / 50) + 1 }} 页 · 本页
            {{ data.items.length }} 条 · 每页最多 50 条</span
          ><el-button
            :disabled="!offset || loading"
            @click="
              offset -= 50;
              load();
            "
            >上一页</el-button
          ><el-button
            :disabled="!data.has_more || loading"
            @click="
              offset += 50;
              load();
            "
            >下一页</el-button
          >
        </div>
      </section>
      <section
        v-if="creating"
        :aria-label="formTitle"
        class="panel catalog-page"
      >
        <div class="catalog-heading">
          <el-button :disabled="busy" @click="cancelForm"
            >← 返回{{ editing ? "资料详情" : "资料列表" }}</el-button
          >
          <h2>{{ formTitle }}</h2>
          <span v-if="dirty" class="form-note">尚未保存</span>
        </div>
        <form
          ref="entryForm"
          class="business-form"
          novalidate
          @keydown="submitCatalogForm"
          @date-invalid="
            (e: Event) => {
              const tab = (e.target as HTMLElement).closest<HTMLElement>(
                '[data-entry-tab]',
              )?.dataset.entryTab;
              if (tab) entryTab = tab;
            }
          "
          @submit.prevent="create()"
        >
          <p v-if="editing" class="form-note">
            身份和资质变更保存后重新进入待审核。推荐级别、分类和日常联系信息可在“经营信息”卡片直接保存。往来单位可扩展为供销双向身份，编码与信用代码保留。
          </p>
          <p v-else class="form-note">
            先保存基本信息，再进入详情卡片补充经营、医保和平台对接资料。新档案保存为待审核状态，由另一位成员核验资质。
          </p>
          <div
            v-if="!product"
            class="catalog-tabs"
            role="tablist"
            aria-label="建档分组"
          >
            <button
              v-for="tab in entryTabs"
              :key="tab.id"
              type="button"
              role="tab"
              :id="`party-entry-${tab.id}`"
              :aria-controls="`party-entry-panel-${tab.id}`"
              :aria-selected="entryTab === tab.id"
              :tabindex="entryTab === tab.id ? 0 : -1"
              @click="entryTab = tab.id"
              @keydown.right.prevent="moveEntryTab(1)"
              @keydown.left.prevent="moveEntryTab(-1)"
            >
              {{ tab.name }}
            </button>
          </div>
          <fieldset
            v-if="product"
            :disabled="busy || (product && rulesLoading)"
            class="catalog-fields form-grid"
          >
            <h3 class="full-width catalog-group-title">
              {{ product ? "商品身份与分类资料" : "单位身份与经营资质" }}
            </h3>
            <label v-if="product"
              >商品类别<select v-model="form.category" :disabled="!!editing">
                <option value="DEVICE">医疗器械</option>
                <option value="DRUG">药品</option>
              </select></label
            >
            <label
              >{{ product ? "商品编码" : "编码"
              }}<input
                v-model="form.code"
                :aria-label="product ? '商品编码' : '编码'"
                :disabled="!!editing"
                :required="!product"
                maxlength="60"
                :placeholder="product ? '留空，保存时自动生成' : ''"
              /><small v-if="product && !editing"
                >自动生成规则在“商品字段设置”中维护。</small
              ></label
            >
            <template v-if="product">
              <label
                >基本单位<DictionaryValue
                  v-if="fieldPolicy?.unit_dictionary"
                  v-model="form.base_unit"
                  :dictionary="fieldPolicy.unit_dictionary"
                  label="基本单位"
                  :disabled="!!editing"
                  required />
                <input
                  v-else
                  v-model="form.base_unit"
                  :disabled="!!editing"
                  required
                  maxlength="30"
              /></label>
              <label
                >数量小数位<select
                  v-model.number="form.qty_scale"
                  :disabled="!!editing"
                >
                  <option v-for="n in 7" :key="n" :value="n - 1">
                    {{ n - 1 }} 位
                  </option>
                </select></label
              >
              <p v-if="rulesLoading" class="full-width form-note">
                正在加载本企业的商品字段设置…
              </p>
              <template v-else-if="fieldPolicy">
                <p class="full-width form-note">
                  {{ entryTemplateName }} · 字段按基本、质量和经营用途分组
                </p>
                <ProductFields
                  v-model="productValues"
                  :fields="basicEntryFields"
                />
                <h3 class="full-width catalog-group-title">质量管控资料</h3>
                <p class="full-width form-note">
                  经营范围类别从数据字典中维护，保存后由质量人员核验；后续经营范围控制将以此为依据。
                </p>
                <ProductFields
                  v-model="productValues"
                  :fields="qualityEntryFields"
                />
                <template v-if="!editing && operationEntryFields.length">
                  <h3 class="full-width catalog-group-title">经营检索资料</h3>
                  <ProductFields
                    v-model="productValues"
                    :fields="operationEntryFields"
                  />
                  <p class="full-width form-note">
                    建档后可在经营信息卡片中调整，无需质量重审。
                  </p>
                </template>
              </template>
              <el-button v-else class="full-width" @click="loadFieldPolicy"
                >重新加载商品字段</el-button
              >
            </template>
            <label v-if="editing" class="full-width"
              >修订原因<input v-model="revisionReason" required maxlength="200"
            /></label>
          </fieldset>
          <fieldset
            v-else
            :disabled="busy"
            class="catalog-fields party-entry-fields"
          >
            <div
              v-show="entryTab === 'basic'"
              id="party-entry-panel-basic"
              data-entry-tab="basic"
              role="tabpanel"
              aria-labelledby="party-entry-basic"
              class="form-grid"
            >
              <label
                >单位类型<select v-model="form.kind">
                  <option
                    v-if="!editing || editing.kind === 'SUPPLIER'"
                    value="SUPPLIER"
                  >
                    供应商
                  </option>
                  <option
                    v-if="!editing || editing.kind === 'CUSTOMER'"
                    value="CUSTOMER"
                  >
                    客户
                  </option>
                  <option value="BOTH">供应商及客户</option>
                </select></label
              >
              <label
                >编码<input
                  v-model="form.code"
                  :disabled="!!editing"
                  required
                  maxlength="60"
              /></label>
              <PartyIdentityFields
                v-model:name="form.name"
                v-model:credit-code="form.credit_code"
                :entity="entity"
                :editing="!!editing"
                @organization="organizationId = $event"
                @existing="openExistingParty"
              />
              <PartyProfileFields
                v-model="form.partyProfile"
                section="basic"
                :qualification-only="!!editing"
              />
              <p class="full-width form-note">
                一个单位可同时作为供应商和客户，共用身份资料。各主体的准入、联系信息和对接编码分别维护。
              </p>
            </div>
            <div
              v-show="entryTab === 'quality'"
              id="party-entry-panel-quality"
              data-entry-tab="quality"
              role="tabpanel"
              aria-labelledby="party-entry-quality"
              class="form-grid"
            >
              <label
                >经营许可 / 备案编号<input
                  v-model="form.license_no"
                  required
                  maxlength="120"
              /></label>
              <label
                >经营类别<select v-model="form.categories" multiple required>
                  <option value="DEVICE">医疗器械</option>
                  <option value="DRUG">药品</option>
                </select></label
              >
              <label
                >资质生效日期<DateInput v-model="form.valid_from" required
              /></label>
              <label
                >资质截止日期<DateInput
                  v-model="form.valid_until"
                  :min="form.valid_from"
                  required
              /></label>
              <PartyProfileFields
                v-model="form.partyProfile"
                section="quality"
                qualification-only
              />
            </div>
            <div
              v-if="!editing"
              v-show="entryTab === 'operations'"
              id="party-entry-panel-operations"
              data-entry-tab="operations"
              role="tabpanel"
              aria-labelledby="party-entry-operations"
              class="form-grid"
            >
              <p class="full-width form-note">
                此处可选填，保存后也可在经营信息卡片直接维护，无需资质重审。
              </p>
              <PartyProfileFields
                v-model="form.partyProfile"
                section="operations"
              />
            </div>
            <label v-if="editing" class="party-revision-reason"
              >修订原因<input v-model="revisionReason" required maxlength="200"
            /></label>
          </fieldset>
          <div class="form-actions">
            <span class="form-note">Ctrl / ⌘ + S 保存资料</span>
            <el-button :disabled="busy" @click="cancelForm">取消</el-button
            ><el-button
              v-if="editing"
              :disabled="busy || (product && (!fieldPolicy || rulesLoading))"
              @click="create(true)"
              >保存并返回列表</el-button
            ><el-button
              type="primary"
              native-type="submit"
              :loading="busy"
              :disabled="product && (!fieldPolicy || rulesLoading)"
              >{{
                editing ? "保存修订并重新送审" : "保存待审核资料"
              }}</el-button
            >
          </div>
        </form>
      </section>
      <section
        v-if="selected"
        aria-label="资料详情"
        class="panel catalog-page"
        v-loading="detailLoading"
      >
        <template v-if="selected">
          <div class="catalog-heading">
            <el-button :disabled="busy || detailLoading" @click="backToList"
              >← 返回资料列表</el-button
            >
            <h2>{{ product ? "商品资料详情" : "往来单位资料详情" }}</h2>
            <el-button @click="infoOpen = true">{{
              product
                ? "商品信息卡"
                : ["SUPPLIER", "BOTH"].includes(selected.kind || "")
                  ? "供应商信息卡"
                  : "客户信息卡"
            }}</el-button>
            <span v-if="hasCodeEdits" class="form-note"
              >有未保存的卡片修改，返回列表时可选择保存或放弃。</span
            >
          </div>
          <div class="catalog-identity">
            <span class="catalog-object-mark" aria-hidden="true">{{
              product ? (selected.category === "DRUG" ? "药" : "械") : "企"
            }}</span>
            <h3>{{ selected.name }}</h3>
            <span class="catalog-code">{{ selected.code }}</span
            ><el-tag>{{
              labels[selected.approval_state || selected.state || ""]
            }}</el-tag>
          </div>
          <div class="catalog-tabs" role="tablist" aria-label="资料分组">
            <button
              v-for="tab in tabs"
              :key="tab.id"
              type="button"
              role="tab"
              :id="`${kind}-tab-${tab.id}`"
              :aria-controls="`${kind}-panel-${tab.id}`"
              :aria-selected="activeTab === tab.id"
              :tabindex="activeTab === tab.id ? 0 : -1"
              @click="selectTab(tab.id)"
              @keydown.right.prevent="moveTab(1)"
              @keydown.left.prevent="moveTab(-1)"
              @keydown.home.prevent="focusTab(tabs[0].id)"
              @keydown.end.prevent="focusTab(tabs[tabs.length - 1].id)"
            >
              {{ tab.name
              }}<span
                v-if="
                  (tab.id === 'operations' && codeEditing.operations) ||
                  (tab.id === 'quality' && codeEditing.certificates)
                "
                aria-label="未保存"
              >
                •</span
              >
            </button>
          </div>
          <div
            v-show="activeTab === 'basic'"
            role="tabpanel"
            :id="`${kind}-panel-basic`"
            :aria-labelledby="`${kind}-tab-basic`"
            class="catalog-card"
          >
            <p v-if="product && detailPolicy" class="form-note">
              {{
                detailPolicy.template.resolved_variant?.name ||
                detailPolicy.template.name
              }}
            </p>
            <dl class="facts">
              <dt>{{ product ? "商品类别" : "单位类型" }}</dt>
              <dd>{{ labels[selected.category || selected.kind || ""] }}</dd>
              <dt>{{ product ? "商品编码" : "编码" }}</dt>
              <dd>{{ selected.code }}</dd>
              <template v-if="product"
                ><dt>{{ detailLabel("manufacturer_name", "生产企业") }}</dt>
                <dd>{{ selected.manufacturer_name }}</dd>
                <dt>{{ detailLabel("specification", "规格") }} / 基本单位</dt>
                <dd>
                  {{ selected.specification }} / {{ selected.base_unit }} ·
                  {{ selected.qty_scale }} 位小数
                </dd></template
              >
              <template v-else
                ><dt>统一社会信用代码</dt>
                <dd>{{ selected.credit_code }}</dd></template
              >
            </dl>
            <ProductProfileDetails
              v-if="product"
              :category="selected.category || ''"
              :profile="readProductProfile(selected)"
              :policy-fields="detailPolicy?.fields"
              :context-values="detailPolicy?.context"
              section="basic"
              qualification-only
            />
            <template v-if="!product">
              <p v-if="selected.organization" class="form-note">
                共享机构：{{ selected.organization.name }} ·
                {{
                  selected.organization.legal_code
                }}。本页名称与资质按本主体的审核记录保存。
              </p>
              <PartyOverview
                :profile="readPartyProfile(selected)"
                :editable="can('catalog.write')"
                :disabled="busy || detailLoading"
                :pending="!!codeEditing.operations"
                @maintain="maintainParty"
                @open="focusTab"
              />
            </template>
            <details v-if="product && hasLegacyCodes" class="legacy-codes">
              <summary>历史库存编码</summary>
              <p class="form-note">
                这些编码来自既有资料，保留用于原库存、条码与单据查询。新商品统一使用商品编码。
              </p>
              <p v-for="sku in selected.skus" :key="sku.id">
                {{ sku.code }} · {{ sku.base_unit }}
              </p>
            </details>
            <div class="business-toolbar">
              <el-button
                v-if="can('catalog.write')"
                :disabled="busy || detailLoading || hasCodeEdits"
                @click="revise"
                >修订资料</el-button
              ><el-button
                :disabled="busy || detailLoading || hasCodeEdits"
                @click="detail(selected)"
                >刷新资料详情</el-button
              ><el-button @click="selectTab('quality')">查看质量管控</el-button>
            </div>
            <template v-if="product"
              ><div v-for="sku in selected.skus || []" :key="sku.id">
                <CatalogCodes
                  kind="skus"
                  :target="sku.id"
                  :category="selected.category"
                  :title="`商品条码 · ${sku.code}`"
                  :schemes="['BARCODE']"
                  @editing="codeEditing[`barcode:${sku.id}`] = $event"
                  @failure="emit('failure', $event)"
                />
                <PackagingPanel
                  :sku="sku.id"
                  @editing="codeEditing[`package:${sku.id}`] = $event"
                  @failure="emit('failure', $event)"
                /></div
            ></template>
          </div>
          <div
            v-show="activeTab === 'quality'"
            role="tabpanel"
            :id="`${kind}-panel-quality`"
            :aria-labelledby="`${kind}-tab-quality`"
            class="catalog-card"
          >
            <h3>质量管控与资质审核</h3>
            <p class="form-note">
              经营范围类别、证照和储存要求集中核验。范围类别的实际业务拦截将在后续接入。
            </p>
            <dl class="facts">
              <dt>编码</dt>
              <dd>{{ selected.code }}</dd>
              <dt>类型</dt>
              <dd>{{ labels[selected.category || selected.kind || ""] }}</dd>
              <template v-if="selected.category === 'DRUG'"
                ><dt>药品销售分类</dt>
                <dd>
                  {{ drugClasses[selected.drug_sale_class || "UNKNOWN"] }}
                </dd></template
              >
              <template v-if="product"
                ><dt>{{ detailLabel("manufacturer_name", "生产企业") }}</dt>
                <dd>{{ selected.manufacturer_name }}</dd>
                <dt>{{ detailLabel("specification", "规格") }} / 单位</dt>
                <dd>
                  {{ selected.specification }} / {{ selected.base_unit }} ·
                  {{ selected.qty_scale }} 位小数
                </dd>
                <dt>
                  {{
                    credentialLabel(selected.category, selected.device_class)
                  }}
                </dt>
                <dd>{{ selected.approval_number || "待补齐" }}</dd>
              </template>
              <template v-else
                ><dt>信用代码</dt>
                <dd>{{ selected.credit_code }}</dd>
                <dt>经营资质</dt>
                <dd>{{ selected.license_no }}</dd>
                <dt>经营范围</dt>
                <dd>
                  {{ categoryNames }}
                </dd></template
              >
              <dt>资质有效期</dt>
              <dd>
                {{ selected.valid_from || "待补齐" }} 至
                {{ selected.valid_until || "待补齐" }}
              </dd>
              <dt>状态</dt>
              <dd>
                {{ labels[selected.approval_state || selected.state || ""] }}
              </dd>
            </dl>
            <ProductProfileDetails
              v-if="product"
              :category="selected.category || ''"
              :profile="readProductProfile(selected)"
              :policy-fields="detailPolicy?.fields"
              :context-values="detailPolicy?.context"
              qualification-only
              section="quality"
            />
            <PartyProfileFields
              v-else
              :model-value="readPartyProfile(selected)"
              readonly
              qualification-only
              section="quality"
            />
            <div class="business-toolbar">
              <el-button
                v-if="can('catalog.write')"
                :disabled="busy || detailLoading || hasCodeEdits"
                @click="revise"
                >修订资料</el-button
              >
              <el-button
                :disabled="busy || detailLoading || hasCodeEdits"
                @click="detail(selected)"
                >刷新资料详情</el-button
              >
            </div>
            <form
              v-if="can('catalog.review')"
              class="business-form"
              @submit.prevent="saveReview"
            >
              <p v-if="cannotApprove" class="form-note">
                您是建档人或参与了本轮修订，请由未参与的成员审核通过；您仍可停用该资料。
              </p>
              <div class="form-grid">
                <label
                  >审核结果<select v-model="review.decision">
                    <option value="APPROVE" :disabled="cannotApprove">
                      审核通过
                    </option>
                    <option value="SUSPEND">停用资料</option>
                  </select></label
                >
                <label
                  >核验凭据编号<input
                    v-model="review.evidence_ref"
                    required
                    maxlength="120"
                    placeholder="纸质档案号或内部核验编号"
                /></label>
                <label class="full-width"
                  >审核说明<input
                    v-model="review.reason"
                    required
                    maxlength="200"
                /></label>
              </div>
              <div class="form-actions">
                <el-button
                  type="primary"
                  native-type="submit"
                  :loading="busy"
                  :disabled="
                    detailLoading ||
                    hasCodeEdits ||
                    (review.decision === 'APPROVE' && cannotApprove)
                  "
                  >提交审核</el-button
                >
              </div>
            </form>
            <ProductCertificates
              v-if="product && visitedTabs.includes('quality')"
              :key="selected.id"
              :product="selected.id"
              :active="activeTab === 'quality'"
              @editing="codeEditing.certificates = $event"
              @failure="emit('failure', $event)"
              @saved="detail(selected)"
            />
            <PartyCertificates
              v-if="!product && visitedTabs.includes('quality')"
              :key="selected.id"
              :party="selected.id"
              :active="activeTab === 'quality'"
              @editing="codeEditing.certificates = $event"
              @failure="emit('failure', $event)"
              @saved="detail(selected)"
            />
          </div>
          <div
            v-if="visitedTabs.includes('operations')"
            v-show="activeTab === 'operations'"
            role="tabpanel"
            :id="`${kind}-panel-operations`"
            :aria-labelledby="`${kind}-tab-operations`"
          >
            <CatalogOperations
              ref="operationsCard"
              :key="selected.id"
              :kind="kind"
              :target="selected.id"
              :active="activeTab === 'operations'"
              :master-version="selected.version"
              @editing="codeEditing.operations = $event"
              @failure="emit('failure', $event)"
              @saved="
                detail(selected);
                load();
              "
            />
          </div>
          <div
            v-if="product && visitedTabs.includes('prices')"
            v-show="activeTab === 'prices'"
            role="tabpanel"
            :id="`${kind}-panel-prices`"
            :aria-labelledby="`${kind}-tab-prices`"
          >
            <CatalogPrices
              v-for="sku in selected.skus || []"
              :key="sku.id"
              :sku="sku.id"
              :code="selected.code"
              :active="activeTab === 'prices'"
              @editing="codeEditing[`price:${sku.id}`] = $event"
              @failure="emit('failure', $event)"
              @saved="detail(selected)"
            />
          </div>
          <template
            v-for="tab in tabs.filter((t) => t.schemes.length)"
            :key="tab.id"
          >
            <div
              v-if="visitedTabs.includes(tab.id)"
              v-show="activeTab === tab.id"
              role="tabpanel"
              :id="`${kind}-panel-${tab.id}`"
              :aria-labelledby="`${kind}-tab-${tab.id}`"
            >
              <template v-if="product"
                ><div v-for="sku in selected.skus || []" :key="sku.id">
                  <CatalogCodes
                    kind="skus"
                    :target="sku.id"
                    :category="selected.category"
                    :title="`${tab.name} · ${sku.code}`"
                    :schemes="tab.schemes"
                    @editing="codeEditing[`${tab.id}:${sku.id}`] = $event"
                    @failure="emit('failure', $event)"
                  /></div
              ></template>
              <CatalogCodes
                v-else
                :key="selected.id"
                kind="parties"
                :target="selected.id"
                :title="tab.name"
                :schemes="tab.schemes"
                @editing="codeEditing[tab.id] = $event"
                @failure="emit('failure', $event)"
              />
            </div>
          </template>
          <div
            v-show="activeTab === 'history'"
            role="tabpanel"
            :id="`${kind}-panel-history`"
            :aria-labelledby="`${kind}-tab-history`"
          >
            <CatalogHistory :events="history" />
            <div class="pager">
              <el-button
                :disabled="busy || detailLoading || !historyOffset"
                @click="
                  historyOffset -= 50;
                  detail(selected, false);
                "
                >上一页记录</el-button
              >
              <el-button
                :disabled="busy || detailLoading || !historyMore"
                @click="
                  historyOffset += 50;
                  detail(selected, false);
                "
                >下一页记录</el-button
              >
            </div>
          </div>
        </template>
      </section>
      <CatalogColumnSettings
        v-if="columnSettings && !product"
        :key="partyColumns.storageKey.value"
        title="往来单位列表列设置"
        :columns="partyColumns.columns.value"
        :definitions="partyListColumns"
        :save="partyColumns.save"
        @close="columnSettings = false"
      />
      <InformationCard
        v-if="selected"
        v-model="infoOpen"
        :kind="product ? 'product' : 'party'"
        :record="{ ...selected }"
        :version="selected.version"
        :state="selected.state || selected.approval_state"
      />
    </div>
  </div>
</template>
<style scoped>
.party-sidebar nav {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 0 6px;
}
.party-sidebar nav button {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 13px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ui-text);
  text-align: left;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.party-sidebar nav button.active {
  background: var(--ui-soft);
  color: var(--ui-accent);
  font-weight: 600;
}
.party-workspace .panel {
  border: 0;
  box-shadow: none;
  margin: 0;
  border-radius: 0;
}
.party-workspace .catalog-heading {
  padding-bottom: 14px;
  border-bottom: 1px solid var(--ui-border);
}
.party-workspace .catalog-tabs {
  border-radius: 0;
  background: transparent;
  border-bottom: 1px solid var(--ui-border);
  padding: 0;
  flex-wrap: nowrap;
  overflow-x: auto;
}
.party-workspace .catalog-tabs button {
  white-space: nowrap;
  border-radius: 0;
  padding: 12px 16px;
  border: 0;
  border-bottom: 3px solid transparent;
}
.party-workspace .catalog-tabs button[aria-selected="true"] {
  border-bottom-color: var(--ui-accent);
  box-shadow: none;
  background: var(--ui-tint);
}
@media (max-width: 800px) {
  .party-workspace {
    grid-template-columns: minmax(0, 1fr);
  }
  .party-sidebar {
    position: static;
  }
  .party-sidebar nav {
    flex-direction: row;
    overflow: auto;
  }
  .party-sidebar nav button {
    white-space: nowrap;
  }
  .party-sidebar .master-side-note {
    display: none;
  }
}
.party-configurable-list :deep(.party-cell-wrap .cell) {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.party-configurable-list :deep(.party-cell-clip .cell) {
  white-space: nowrap;
}
.party-configurable-list :deep(.party-cell-clip .cell-sub) {
  overflow: hidden;
  text-overflow: ellipsis;
}
.catalog-identity {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0 16px;
  padding: 14px 16px;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: linear-gradient(110deg, var(--ui-tint), #fff);
}
.catalog-identity h3 {
  margin: 0;
  font-size: 18px;
  overflow-wrap: anywhere;
  min-width: 0;
}
.catalog-identity .catalog-code {
  font-size: 13px;
  color: var(--ui-muted);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.catalog-object-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 8px;
  color: var(--ui-accent);
  background: var(--ui-soft);
  border: 1px solid var(--ui-accent-line);
  font-size: 17px;
  font-weight: 600;
}
.catalog-tabs {
  display: flex;
  overflow-x: auto;
  gap: 3px;
  padding: 4px;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  margin-top: 12px;
  scrollbar-width: thin;
}
.catalog-tabs button {
  white-space: nowrap;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  padding: 8px 16px;
  color: var(--ui-muted);
  font: inherit;
  cursor: pointer;
}
.catalog-tabs button[aria-selected="true"] {
  border-color: var(--ui-border);
  color: var(--el-color-primary);
  background: #fff;
  box-shadow: 0 1px 3px #22344a0d;
  font-weight: 600;
}
.catalog-tabs button:hover {
  color: var(--ui-accent);
  background: #fff;
}
.catalog-tabs button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -3px;
}
.catalog-card {
  padding-top: 12px;
}
.catalog-fields {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.catalog-heading {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.catalog-heading h2 {
  margin: 0;
  font-size: 18px;
}
.catalog-heading .form-note {
  margin: 0;
}
.catalog-group-title {
  font-size: 15px;
  margin: 6px 0;
}
.catalog-page {
  max-width: none;
}
.catalog-page .business-form {
  max-width: none;
}
.catalog-page :deep(.form-grid) {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px 20px;
}
.party-entry-fields {
  padding: 16px 0;
}
.party-revision-reason {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}
.catalog-page :deep(.facts) {
  max-width: 100%;
  grid-template-columns: repeat(3, minmax(110px, 0.45fr) minmax(0, 1fr));
  column-gap: 16px;
  row-gap: 14px;
  padding: 16px;
  background: var(--ui-tint);
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  margin: 16px 0 20px;
  line-height: 1.6;
}
.catalog-page :deep(.product-profile-details) {
  border-top: 1px solid var(--line);
  margin-top: 16px;
  padding-top: 8px;
}
.catalog-page :deep(.product-profile-details h3) {
  font-size: 15px;
}
.catalog-page :deep(.catalog-price-card),
.catalog-page :deep(.operations-card) {
  padding: 12px 0 0;
}
.catalog-page :deep(.catalog-price-card h3),
.catalog-page :deep(.operations-card h3),
.catalog-card > h3 {
  font-size: 16px;
  margin: 4px 0 12px;
}
.catalog-card > h3::before {
  content: "";
  display: inline-block;
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: var(--ui-accent);
  margin-right: 8px;
  vertical-align: -1px;
}
.catalog-page :deep(.operations-card .business-toolbar) {
  align-items: center;
}
.catalog-page :deep(.operations-card .business-toolbar h3),
.catalog-page :deep(.operations-card .business-toolbar .form-note) {
  margin: 0;
}
.catalog-page :deep(.form-actions) {
  border-top: 1px solid var(--ui-border);
  padding-top: 14px;
}
.catalog-list .business-toolbar {
  padding: 14px;
  background: var(--ui-tint);
  border: 1px solid var(--ui-border);
  border-radius: 8px;
}
.catalog-list .business-toolbar label {
  width: auto;
  min-width: 140px;
  flex: 1 1 150px;
}
.catalog-list .business-toolbar label:has(input[aria-label="查询资料"]) {
  flex: 2 1 300px;
}
.catalog-page :deep(dd) {
  overflow-wrap: anywhere;
}
.catalog-page > .business-form > .form-actions {
  position: sticky;
  bottom: 0;
  background: var(--surface, #fff);
  padding: 14px 0;
  border-top: 1px solid var(--line);
  z-index: 2;
}
.catalog-list input[aria-label="查询资料"] {
  min-width: 270px;
}
@media (max-width: 900px) {
  .catalog-page :deep(.facts) {
    grid-template-columns: repeat(2, minmax(100px, 0.45fr) minmax(0, 1fr));
  }
  .catalog-page :deep(.form-grid) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .catalog-identity {
    padding: 12px;
    gap: 8px;
  }
  .catalog-identity h3 {
    flex: 1 1 calc(100% - 50px);
    font-size: 16px;
  }
  .catalog-list .business-toolbar label,
  .catalog-list .business-toolbar label:has(input[aria-label="查询资料"]) {
    flex-basis: 100%;
    min-width: 0;
  }
  .catalog-page :deep(.facts) {
    padding: 12px;
    column-gap: 10px;
  }
  .catalog-page :deep(.facts) {
    grid-template-columns: 100px minmax(0, 1fr);
  }
  .catalog-page :deep(.form-grid) {
    grid-template-columns: 1fr;
  }
  .catalog-list input[aria-label="查询资料"] {
    min-width: 0;
  }
}
</style>
