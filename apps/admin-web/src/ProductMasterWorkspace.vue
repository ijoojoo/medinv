<script setup lang="ts">
import MasterDataLayout from "./MasterDataLayout.vue";
import CatalogAttachments from "./CatalogAttachments.vue";
import { copyMasterCode, type MasterTool } from "./masterTools";
import MasterChangeSummary from "./MasterChangeSummary.vue";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { request, session } from "./api";
import { can, labels, useCommand } from "./business";
import { confirmDates } from "./dateControls";
import { parseDate } from "./dates";
import { registerCatalogChanges } from "./catalogChanges";
import { catalogSaveKey, useCatalogListReturn } from "./catalogInteraction";
import MasterFields from "./MasterFields.vue";
import type { CatalogDictionary } from "./catalogDictionaries";
import MasterTree from "./MasterTree.vue";
import InformationCard from "./InformationCard.vue";
const infoOpen = ref(false);
const editMode = ref(false),
  historyListOpen = ref(false),
  attachmentsOpen = ref(false);
const displayedValues = computed(() =>
  !editMode.value && record.value ? record.value.active_values : values.value,
);
import CatalogColumnSettings from "./CatalogColumnSettings.vue";
import { useCatalogColumns, type ListColumnDefinition } from "./catalogColumns";
import ProductCertificates from "./ProductCertificates.vue";
import CatalogCodes from "./CatalogCodes.vue";
import CatalogPrices from "./CatalogPrices.vue";
import CatalogOperations from "./CatalogOperations.vue";
import ProductEntitySettings from "./ProductEntitySettings.vue";
import PackagingPanel from "./PackagingPanel.vue";
import {
  baseBarcodes,
  inlinePrices,
  priceLabels,
  tradeChanges,
} from "./masterTrade";
import {
  descendants,
  nodePath,
  roleFor,
  summary,
  downloadCsv,
  type SchemaResponse,
  type MasterRecord,
  type MasterList,
  type MasterRow,
  type MasterValues,
  type MasterIssue,
  type Organization,
  type RoleLink,
} from "./productMaster";
import { clearReference, type ReferenceTarget } from "./workspace";
const props = defineProps<{
  active?: boolean;
  initialTarget?: ReferenceTarget;
}>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const schema = ref<SchemaResponse | null>(null),
  list = ref<MasterList | null>(null),
  record = ref<MasterRecord | null>(null);
const selected = ref(""),
  expanded = ref<string[]>([]),
  search = ref(""),
  offset = ref(0),
  sort = ref("code"),
  state = ref("");
const editing = ref(false),
  node = ref(""),
  tab = ref("basic"),
  values = ref<MasterValues>({}),
  original = ref(""),
  issues = ref<MasterIssue[]>([]),
  pinnedRevision = ref(0);
const loading = ref(false),
  relation = ref(""),
  childDirty = ref(false),
  organizations = ref<Organization[]>([]),
  orgSearch = ref("");
const listRoot = ref<HTMLElement>();
const listReturn = useCatalogListReturn(listRoot);
const applied = ref({ search: "", state: "", sort: "code" });
const columnSettings = ref(false);
const columnDefinitions = computed<ListColumnDefinition[]>(() => {
  const base: ListColumnDefinition[] = [
    { key: "code", label: "商品编码", width: 150, required: true },
    { key: "name", label: "规范名称", width: 240, required: true },
    { key: "category_path", label: "实际品类", width: 180 },
    { key: "specification", label: "规格", width: 170 },
    { key: "base_unit", label: "单位 / 计费单位", width: 135 },
    {
      key: "regulatory_subject",
      label: "上市许可持有人 / 注册人 / 备案人",
      width: 270,
    },
    { key: "manufacturer", label: "生产企业", width: 240 },
    { key: "product_certificates", label: "产品证件", width: 220 },
    { key: "approval_state", label: "资料审核", width: 110, required: true },
  ];
  const keys = new Set(base.map((c) => c.key));
  const available = list.value?.columns || schema.value?.payload.columns || [];
  for (const c of available) {
    if (
      !keys.has(c.key) &&
      (c.value_type !== "collection" || c.key === "barcodes")
    ) {
      base.push({ key: c.key, label: c.label, width: 180, visible: false });
      keys.add(c.key);
    }
  }
  return base;
});
const listColumns = useCatalogColumns(ref("products"), columnDefinitions);
function listCell(row: MasterRow, key: string) {
  if (key === "category_path") return row.category_path.slice(1).join(" / ");
  if (key === "approval_state")
    return `${labels[row.approval_state] || row.approval_state}${row.has_changes ? " · 变更待审" : ""}`;
  const stateLabels: Record<string, string> = {
    NOT_APPLICABLE: "本品类不适用",
    NO_PERMISSION: "无查看权限",
    MISSING: "未填写",
    UNCONFIRMED: "待核实",
  };
  if (stateLabels[row.states[key]]) return stateLabels[row.states[key]];
  const options = schema.value?.payload.fields.find(
    (f) => f.key === key,
  )?.options;
  return (
    options?.find((option) => option.value === row.values[key])?.label ||
    summary(row.values[key])
  );
}
const opening = ref(false);
const savedRefreshId = ref("");
let detailEpoch = 0;
const chooseCategory = ref(false),
  newNode = ref(""),
  importOpen = ref(false),
  csv = ref(""),
  importResult = ref<{
    valid: boolean;
    count: number;
    errors: { row: number; message: string }[];
  } | null>(null);
const certificates = ref<
    {
      id: string;
      product_id: string;
      version: string;
      number: string;
      certificate_type: string;
      holder_name: string;
      state: string;
    }[]
  >([]),
  certSearch = ref("");
const historyOpen = ref(false),
  historyValues = ref<Record<string, unknown> | null>(null);
const historyLabels = ref<Record<string, string>>({});
const nestedDirty = ref<Record<string, boolean>>({});
const leaveOpen = ref(false);
let resolveLeave: ((allowed: boolean) => void) | null = null;
const relationDirty = computed(
  () => childDirty.value || Object.values(nestedDirty.value).some(Boolean),
);
const roleDraft = ref<RoleLink[]>([]),
  certificateDraft = ref<
    {
      certificate_id: string;
      certificate_version: number;
      business_use?: boolean;
    }[]
  >([]);
const newOrg = ref({
  name: "",
  legal_code: "",
  kind: "ORGANIZATION",
  parent_id: null as string | null,
});
const reason = ref("维护商品主数据"),
  reviewReason = ref(""),
  reviewOpen = ref(false),
  reviewDecision = ref("APPROVE");
const { busy, execute } = useCommand((e) => emit("failure", e));
const barcode = ref("");
const prices = ref(inlinePrices({}));
const singleUnit = computed(
  () => !record.value || record.value.skus.length === 1,
);
const tradeOriginal = ref<MasterValues>({});
const tradeBody = computed(() =>
  singleUnit.value
    ? tradeChanges(
        tradeOriginal.value,
        barcode.value,
        prices.value,
        reason.value,
      )
    : {},
);
const tradeDirty = computed(() => Object.keys(tradeBody.value).length > 0);
function resetTrade(source: MasterValues) {
  tradeOriginal.value = JSON.parse(JSON.stringify(source));
  barcode.value = baseBarcodes(source)[0]?.external_code || "";
  prices.value = inlinePrices(source);
}
const dirty = computed(
  () =>
    editing.value &&
    (!!savedRefreshId.value ||
      JSON.stringify(values.value) !== original.value ||
      tradeDirty.value),
);
const profile = computed(
  () =>
    schema.value?.payload.nodes.find((n) => n.node_id === node.value)
      ?.field_profile_id || "drug",
);
const path = computed(() =>
  schema.value ? nodePath(schema.value.payload, node.value) : [],
);
const options = computed(
  () =>
    schema.value?.payload.nodes.filter(
      (n) =>
        n.is_assignable &&
        n.enabled &&
        descendants(schema.value!.payload, selected.value).has(n.node_id),
    ) || [],
);
const roleOptions = computed(() =>
  relation.value === "manufacturer"
    ? ["MANUFACTURER", "CONTRACT_MANUFACTURER"]
    : relation.value === "service_provider"
      ? ["SERVICE_PROVIDER"]
      : [roleFor(profile.value, values.value)].filter((v): v is string => !!v),
);
const legacyLabels: Record<string, string> = {
  manufacturer_name: "原生产企业名称",
  authorization_holder: "原持有人名称",
  registrant_name: "原注册 / 备案人名称",
  approval_number: "原证照编号",
  valid_from: "原有效期起",
  valid_until: "原有效期止",
};
const dataKey = `master-tree:${session.value?.tenant_id}`;
let epoch = 0;
onUnmounted(() => {
  epoch++;
  detailEpoch++;
});
onUnmounted(
  registerCatalogChanges(
    () => ({
      dirty: dirty.value || relationDirty.value,
      busy: busy.value,
    }),
    "products",
    async () => {
      if (relationDirty.value) {
        ElMessage.warning("请先保存当前关系或业务卡片中的修改。");
        return false;
      }
      return save();
    },
  ),
);
function beforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value || relationDirty.value) {
    e.preventDefault();
    e.returnValue = "";
  }
}
onMounted(() => {
  window.addEventListener("beforeunload", beforeUnload);
  void init().then(locateReference);
});
onUnmounted(() => window.removeEventListener("beforeunload", beforeUnload));
async function locateReference() {
  const target = props.initialTarget;
  if (!target || !schema.value) return;
  if (await open(target.id)) {
    if (target.tab === "certificates") tab.value = "quality";
    if (target.tab === "history") {
      historyOpen.value = false;
      historyListOpen.value = true;
    }
  }
}
watch(
  () => props.initialTarget?.token,
  () => {
    if (props.initialTarget) void locateReference();
  },
);
async function init() {
  try {
    schema.value = await request<SchemaResponse>(
      "/api/v1/catalog/master/schema",
    );
    selected.value = schema.value.payload.nodes[0].node_id;
    try {
      expanded.value =
        JSON.parse(localStorage.getItem(dataKey) || "null") ||
        schema.value.payload.nodes
          .filter((n) => !n.is_assignable)
          .map((n) => n.node_id);
    } catch {
      expanded.value = [selected.value];
    }
    await load();
  } catch (e) {
    emit("failure", e);
  }
}
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<MasterList>(
      `/api/v1/catalog/master/products?${params()}`,
    );
    if (current === epoch) {
      if (offset.value && offset.value >= result.total) {
        offset.value = Math.max(0, Math.ceil(result.total / 50) - 1) * 50;
        await load();
        return;
      }
      list.value = result;
    }
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function params(limit = 50, start = offset.value) {
  return new URLSearchParams({
    node: selected.value,
    search: applied.value.search,
    offset: String(start),
    limit: String(limit),
    sort: applied.value.sort,
    state: applied.value.state,
  }).toString();
}
function query(reset = false) {
  if (reset) {
    search.value = "";
    state.value = "";
    sort.value = "code";
  }
  applied.value = {
    search: search.value.trim(),
    state: state.value,
    sort: sort.value,
  };
  offset.value = 0;
  listReturn.clear();
  void load();
}
function expand(id: string) {
  expanded.value = expanded.value.includes(id)
    ? expanded.value.filter((x) => x !== id)
    : [...expanded.value, id];
  try {
    localStorage.setItem(dataKey, JSON.stringify(expanded.value));
  } catch {
    /* Navigation remains usable without local persistence. */
  }
}
async function browse(id: string) {
  if (editing.value) {
    await close();
    if (editing.value) return;
  }
  selected.value = id;
  offset.value = 0;
  listReturn.clear();
  void load();
}
async function guard() {
  if (busy.value || opening.value) return false;
  if (!dirty.value && !relationDirty.value) return true;
  leaveOpen.value = true;
  return new Promise<boolean>((resolve) => {
    resolveLeave = resolve;
  });
}
async function leaveChoice(action: "cancel" | "discard" | "save") {
  if (busy.value) return;
  if (action === "save" && !(await save())) return;
  leaveOpen.value = false;
  const resolve = resolveLeave;
  resolveLeave = null;
  resolve?.(action !== "cancel");
}
async function newProduct() {
  if (!(await guard())) return;
  if (!editing.value) listReturn.capture();
  try {
    await refreshDictionaries();
  } catch (e) {
    emit("failure", e);
    return;
  }
  const n = schema.value?.payload.nodes.find(
    (n) => n.node_id === selected.value,
  );
  if (n?.enabled && n.is_assignable) start(n.node_id);
  else {
    newNode.value = "";
    chooseCategory.value = true;
  }
}
async function refreshDictionaries() {
  const result = await request<{ items: CatalogDictionary[] }>(
    "/api/v1/catalog/dictionaries",
  );
  if (!schema.value?.dictionaries) return;
  const mapped = schema.value.dictionaries;
  schema.value.dictionaries = Object.fromEntries(
    result.items.filter((d) => d.key in mapped).map((d) => [d.key, d]),
  );
}
function start(id: string) {
  editMode.value = true;
  savedRefreshId.value = "";
  node.value = id;
  record.value = null;
  values.value = { record_status: "ACTIVE" };
  resetTrade(values.value);
  original.value = JSON.stringify(values.value);
  issues.value = [];
  pinnedRevision.value = schema.value!.revision;
  editing.value = true;
  chooseCategory.value = false;
  childDirty.value = false;
  tab.value = "basic";
  void loadOrganizations();
}
async function open(id: string, afterSave = false) {
  if (!afterSave && !(await guard())) return;
  if (!editing.value) listReturn.capture(id);
  const current = ++detailEpoch;
  const previousTab = tab.value;
  opening.value = true;
  try {
    const [result] = await Promise.all([
      request<MasterRecord>(`/api/v1/catalog/master/products/${id}`),
      refreshDictionaries(),
    ]);
    if (current !== detailEpoch) return false;
    record.value = result;
    if (!afterSave) editMode.value = false;
    node.value = result.category_node_id;
    values.value = JSON.parse(JSON.stringify(result.values));
    resetTrade(result.values);
    original.value = JSON.stringify(values.value);
    issues.value = [];
    pinnedRevision.value = result.form_revision;
    editing.value = true;
    tab.value = afterSave ? previousTab : "basic";
    childDirty.value = false;
    await loadOrganizations();
    savedRefreshId.value = "";
    return true;
  } catch (e) {
    if (current === detailEpoch) emit("failure", e);
    return false;
  } finally {
    if (current === detailEpoch) opening.value = false;
  }
}
async function close() {
  if (await guard()) {
    editing.value = false;
    clearReference("products");
    savedRefreshId.value = "";
    record.value = null;
    childDirty.value = false;
    await load();
    await listReturn.restore();
  }
}
async function saveAndReturn() {
  if (await save()) await close();
}
function body() {
  const changed: MasterValues = {};
  for (const [key, value] of Object.entries(values.value)) {
    const old = JSON.parse(original.value || "{}");
    if (!record.value || JSON.stringify(old[key]) !== JSON.stringify(value))
      changed[key] = value;
  }
  return {
    product_id: record.value?.id || null,
    category_node_id: node.value,
    form_revision: pinnedRevision.value,
    expected_version: record.value?.version || 0,
    values: changed,
    reason: reason.value,
    ...tradeBody.value,
  };
}
async function confirmMasterDates() {
  if (!schema.value) return false;
  for (const field of schema.value.payload.fields.filter(
    (f) => f.value_type === "date",
  )) {
    const result = parseDate(values.value[field.key]);
    if (result.error) {
      const slot = schema.value.payload.slots.find(
        (s) => s.field_key === field.key,
      );
      if (slot) {
        tab.value = slot.tab;
        await nextTick();
        confirmDates(
          document.querySelector(
            '.master-fixed-grid[data-tab="' + slot.tab + '"]',
          ),
        );
      }
      return false;
    }
    if (
      values.value[field.key] !== undefined &&
      values.value[field.key] !== null
    )
      values.value[field.key] = result.value || null;
  }
  return true;
}
async function save() {
  if (!editMode.value) return false;
  if (busy.value || opening.value || !can("catalog.write")) return false;
  // The write already succeeded. Retrying must only reload, never create twice.
  if (savedRefreshId.value) return !!(await open(savedRefreshId.value, true));
  if (tradeDirty.value) {
    if (barcode.value && !/^[^\s\x00-\x1f\x7f]{1,128}$/.test(barcode.value)) {
      tab.value = "basic";
      ElMessage.warning("条码请按包装原文填写，不含空格，最多128个字符。");
      return false;
    }
    if (
      Object.keys(priceLabels).some((k) => {
        const value = prices.value[k as keyof typeof priceLabels];
        return (
          value !== "" && !/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$/.test(value)
        );
      })
    ) {
      tab.value = "price";
      ElMessage.warning("价格请输入非负数，最多6位小数；留空表示未设置。");
      return false;
    }
  }
  if (!(await confirmMasterDates())) return false;
  let saved = false;
  await execute<{ id: string }>(
    "/api/v1/catalog/master/products",
    body(),
    async (result) => {
      savedRefreshId.value = result.id;
      const refreshed = await open(result.id, true);
      await load();
      saved = !!refreshed;
    },
  );
  return saved;
}
async function validate() {
  if (!(await confirmMasterDates())) return;
  try {
    const result = await request<{ valid: boolean; issues: MasterIssue[] }>(
      "/api/v1/catalog/master/validate",
      { method: "POST", body: JSON.stringify(body()) },
    );
    issues.value = result.issues;
    if (result.valid) ElMessage.success("全部页签校验通过");
    else focusIssue(result.issues[0]);
  } catch (e) {
    emit("failure", e);
  }
}
async function reloadSchema() {
  try {
    schema.value = await request<SchemaResponse>(
      "/api/v1/catalog/master/schema",
    );
    pinnedRevision.value = schema.value.revision;
    await validate();
  } catch (e) {
    emit("failure", e);
  }
}
async function focusIssue(issue: MasterIssue) {
  tab.value = issue.tab;
  await nextTick();
  document.getElementById(`master-${issue.slot_id}`)?.focus();
}
async function loadOrganizations() {
  const r = await request<{ items: Organization[] }>(
    `/api/v1/catalog/master/organizations?search=${encodeURIComponent(orgSearch.value)}`,
  );
  organizations.value = r.items;
}
async function loadCertificates() {
  const r = await request<{ items: typeof certificates.value }>(
    `/api/v1/catalog/master/certificates?search=${encodeURIComponent(certSearch.value)}${record.value ? `&product_id=${record.value.id}` : ""}`,
  );
  certificates.value = r.items;
}
async function openRelation(key: string) {
  if ((key === "barcodes" || key === "packaging") && tradeDirty.value) {
    ElMessage.warning("请先保存已填写的条码和价格，再维护其他包装或历史条码。");
    return;
  }
  if (
    [
      "manufacturer",
      "service_provider",
      "regulatory_subject",
      "product_certificates",
    ].includes(key)
  ) {
    const pending = () =>
      ((values.value[key] || []) as Record<string, unknown>[]).some((v) =>
        key === "product_certificates" ? !v.certificate_id : !v.organization_id,
      );
    if (pending()) {
      await save();
      if (pending()) return;
    }
  }
  relation.value = key;
  childDirty.value = false;
  nestedDirty.value = {};
  try {
    if (
      ["manufacturer", "service_provider", "regulatory_subject"].includes(key)
    ) {
      roleDraft.value = JSON.parse(JSON.stringify(values.value[key] || []));
      await loadOrganizations();
    }
    if (key === "product_certificates") {
      certificateDraft.value = (
        (values.value[key] || []) as {
          certificate_id: string;
          certificate_version: number;
          business_use?: boolean;
        }[]
      ).map((c) => ({
        certificate_id: c.certificate_id,
        certificate_version: c.certificate_version,
        business_use: c.business_use || false,
      }));
      await loadCertificates();
    }
  } catch (e) {
    emit("failure", e);
  }
}
function useRelations() {
  if (Object.values(nestedDirty.value).some(Boolean)) {
    ElMessage.warning(
      "请先保存或放弃当前关联资料中的未保存修改，再应用关系选择",
    );
    return;
  }
  if (relation.value === "product_certificates")
    values.value[relation.value] = JSON.parse(
      JSON.stringify(certificateDraft.value),
    );
  else
    values.value[relation.value] = roleDraft.value.map((r) => ({
      ...r,
      organization_name:
        organizations.value.find((o) => o.id === r.organization_id)?.name ||
        r.organization_name,
    }));
  relation.value = "";
  childDirty.value = false;
}
async function createOrg() {
  await execute<{ id: string }>(
    "/api/v1/catalog/master/organizations",
    newOrg.value,
    async () => {
      newOrg.value = {
        name: "",
        legal_code: "",
        kind: "ORGANIZATION",
        parent_id: null,
      };
      await loadOrganizations();
    },
  );
}
async function closeRelation(done: () => void) {
  if (relationDirty.value) {
    try {
      await ElMessageBox.confirm(
        "关系或关联资料尚未保存，确定放弃当前窗口中的修改？",
        "关闭维护窗口",
        {
          confirmButtonText: "放弃",
          cancelButtonText: "继续维护",
          closeOnClickModal: false,
        },
      );
    } catch {
      return;
    }
  }
  childDirty.value = false;
  nestedDirty.value = {};
  done();
  if (record.value && !tradeDirty.value) {
    const id = record.value.id;
    try {
      const latest = await request<MasterRecord>(
        `/api/v1/catalog/master/products/${id}`,
      );
      if (record.value?.id !== id || tradeDirty.value || !editing.value) return;
      const previous = JSON.parse(original.value);
      for (const key of ["barcodes", "reference_prices"]) {
        values.value[key] = latest.values[key];
        previous[key] = latest.values[key];
      }
      original.value = JSON.stringify(previous);
      resetTrade(latest.values);
    } catch (e) {
      emit("failure", e);
    }
  }
}
async function exportRows() {
  try {
    const data = await request<MasterList>(
      `/api/v1/catalog/master/products?${params(5000, 0)}`,
    );
    if (data.has_more) {
      ElMessage.warning(
        "筛选结果超过 5000 条，请缩小范围后导出，避免不完整文件",
      );
      return;
    }
    const columns = data.columns;
    const states: Record<string, string> = {
      NOT_APPLICABLE: "本品类不适用",
      NO_PERMISSION: "无查看权限",
      MISSING: "未填写",
      UNCONFIRMED: "待确认",
    };
    downloadCsv("商品主数据查询.csv", [
      [
        "商品身份",
        "品类路径",
        "审核状态",
        ...columns.map((c) => `${c.label} [${c.key}]`),
      ],
      ...data.items.map((row) => [
        row.id,
        row.category_path.join(" / "),
        labels[row.approval_state] || row.approval_state,
        ...columns.map(
          (c) => states[row.states[c.key]] || summary(row.values[c.key]),
        ),
      ]),
    ]);
  } catch (e) {
    emit("failure", e);
  }
}
function importBody() {
  return {
    category_node_id: newNode.value,
    form_revision: schema.value?.revision,
    content: csv.value,
    reason: "主数据 CSV 导入",
  };
}
async function previewImport() {
  try {
    importResult.value = await request(
      "/api/v1/catalog/master/import/preview",
      { method: "POST", body: JSON.stringify(importBody()) },
    );
  } catch (e) {
    emit("failure", e);
  }
}
async function submitImport() {
  await execute("/api/v1/catalog/master/import", importBody(), async () => {
    importOpen.value = false;
    csv.value = "";
    importResult.value = null;
    await load();
  });
}
async function readFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) {
    if (file.size > 262144) {
      ElMessage.warning("文件不得超过 256KB");
      return;
    }
    csv.value = await file.text();
    importResult.value = null;
  }
}
function template() {
  downloadCsv("商品主数据导入模板.csv", [
    ["code", "name", "base_unit", "regulatory_subtype", "specification"],
  ]);
}
async function history(version: number) {
  try {
    const result = await request<{
      snapshot: Record<string, unknown>;
      field_labels: Record<string, string>;
    }>(
      `/api/v1/catalog/master/products/${record.value?.id}/history/${version}`,
    );
    historyValues.value = result.snapshot;
    historyLabels.value = result.field_labels;
    historyOpen.value = true;
  } catch (e) {
    emit("failure", e);
  }
}
async function review() {
  if (!record.value) return;
  await execute(
    `/api/v1/catalog/products/${record.value.id}/review`,
    {
      expected_version: String(record.value.version),
      decision: reviewDecision.value,
      reason: reviewReason.value,
      evidence_ref: reviewReason.value,
    },
    async () => {
      reviewOpen.value = false;
      original.value = JSON.stringify(values.value);
      await open(record.value!.id, true);
      await load();
    },
  );
}
async function certificateSaved() {
  await loadCertificates();
  if (record.value) {
    const latest = await request<MasterRecord>(
      `/api/v1/catalog/master/products/${record.value.id}`,
    );
    record.value.version = latest.version;
    record.value.has_changes = latest.has_changes;
    const revisions = new Map(certificates.value.map((c) => [c.id, c.version]));
    for (const link of (values.value.product_certificates || []) as Record<
      string,
      unknown
    >[]) {
      const version = revisions.get(String(link.certificate_id));
      if (version !== undefined) link.certificate_version = Number(version);
    }
  }
}

const tools = computed<MasterTool[]>(() => {
  const waiting =
    busy.value || opening.value || loading.value ? "正在处理，请稍候" : "";
  const need =
    waiting || (!record.value || !editing.value ? "请先打开一份商品资料" : "");
  const task =
    waiting || (editMode.value && editing.value ? "请先结束修订任务" : "");
  return [
    {
      key: "revise",
      label: "修订资料",
      short: "修订",
      icon: "✎",
      group: "资料维护",
      disabled: !can("catalog.write") ? "无维护权限" : need || task,
    },
    {
      key: "refresh",
      label: "刷新资料",
      icon: "↻",
      group: "资料维护",
      disabled: task,
    },
    {
      key: "new",
      label: "新建商品 / 项目",
      short: "新建",
      icon: "＋",
      group: "资料维护",
      disabled: !can("catalog.write") ? "无维护权限" : task,
    },
    {
      key: "review",
      label: "质量审核 / 停用",
      icon: "✓",
      group: "资料维护",
      disabled: !can("catalog.review") ? "无审核权限" : need || task,
    },
    {
      key: "history",
      label: "变更记录",
      short: "记录",
      icon: "◷",
      group: "关联查阅",
      disabled: need || task,
    },
    {
      key: "attachments",
      label: "证照附件",
      short: "附件",
      icon: "▤",
      group: "关联查阅",
      disabled: need || task,
    },
    {
      key: "card",
      label: "商品信息卡",
      icon: "▣",
      group: "关联查阅",
      disabled: need || task,
    },
    {
      key: "copyCode",
      label: "复制商品编码",
      icon: "⧉",
      group: "辅助操作",
      disabled: need,
    },
    {
      key: "columns",
      label: "列表列设置",
      icon: "☷",
      group: "辅助操作",
      disabled: task,
    },
    {
      key: "import",
      label: "导入商品",
      icon: "↓",
      group: "辅助操作",
      disabled: !can("catalog.write") ? "无维护权限" : task,
    },
    {
      key: "export",
      label: "导出当前范围",
      icon: "↑",
      group: "辅助操作",
      disabled: task,
    },
  ];
});
async function cancelRevision() {
  if (!(await guard())) return;
  if (!record.value) {
    editing.value = false;
    editMode.value = false;
    values.value = {};
    original.value = "{}";
    resetTrade({});
    await listReturn.restore();
    return;
  }
  values.value = JSON.parse(JSON.stringify(record.value.values));
  resetTrade(values.value);
  original.value = JSON.stringify(values.value);
  reason.value = "";
  editMode.value = false;
}
async function toolAction(key: string) {
  if (tools.value.find((t) => t.key === key)?.disabled) return;
  if (key === "copyCode")
    return copyMasterCode(record.value?.active_values.code);
  if (key === "revise") {
    editMode.value = true;
    return;
  }
  if (key === "refresh") {
    if (record.value && editing.value) await open(record.value.id, true);
    else await load();
    return;
  }
  if (key === "new") return newProduct();
  if (key === "history") {
    historyOpen.value = false;
    historyListOpen.value = true;
  }
  if (key === "attachments") attachmentsOpen.value = true;
  if (key === "card") infoOpen.value = true;
  if (key === "review") {
    reviewReason.value = "";
    reviewOpen.value = true;
  }
  if (key === "columns") columnSettings.value = true;
  if (key === "import") {
    newNode.value = "";
    importOpen.value = true;
  }
  if (key === "export") await exportRows();
}
</script>
<template>
  <MasterDataLayout
    :active="active"
    v-if="schema"
    label="商品主数据工作台"
    :tools="tools"
    @action="toolAction"
  >
    <aside class="master-sidebar">
      <div class="master-side-heading">商品与项目 <span>品类导航</span></div>
      <MasterTree
        :nodes="schema.payload.nodes"
        :parent="null"
        :selected="selected"
        :expanded="expanded"
        :counts="list?.counts || {}"
        @select="browse"
        @expand="expand"
      />
      <p class="master-side-note">
        节点名称切换查询范围；箭头只展开目录。当前编辑商品的品类保持独立。
      </p>
    </aside>
    <div class="master-main">
      <div
        v-if="!editing"
        ref="listRoot"
        class="master-browser"
        v-loading="loading || opening"
      >
        <div class="master-toolbar">
          <div>
            <h2>商品主数据</h2>
            <small
              >{{ list?.scope.join(" / ") }} · {{ list?.total || 0 }} 项</small
            >
          </div>
        </div>
        <form class="master-search" @submit.prevent="query()">
          <input
            v-model="search"
            data-catalog-search
            maxlength="200"
            @keydown="listReturn.keys"
            aria-label="检索商品主数据"
            placeholder="编码、名称、通用名、助记码、条码…"
          /><select v-model="state" aria-label="审核状态">
            <option value="">全部审核状态</option>
            <option value="DRAFT">待审核</option>
            <option value="APPROVED">已审核</option>
            <option value="SUSPENDED">已停用</option></select
          ><select v-model="sort" aria-label="商品排序">
            <option value="code">按编码</option>
            <option value="name">按名称</option>
            <option value="updated_at">按修改时间</option>
            <option value="service_price">按服务参考价</option></select
          ><el-button native-type="submit" :loading="loading">查询</el-button>
          <el-button :disabled="loading" @click="query(true)">重置</el-button>
        </form>
        <div class="master-table-scroll">
          <table
            class="master-list-table configurable-master-list"
            :style="{
              width:
                listColumns.visible.value.reduce(
                  (sum, c) => sum + c.width,
                  90,
                ) + 'px',
            }"
          >
            <colgroup>
              <col
                v-for="column in listColumns.visible.value"
                :key="column.key"
                :style="{ width: column.width + 'px' }"
              />
              <col style="width: 90px" />
            </colgroup>
            <thead>
              <tr>
                <th
                  v-for="column in listColumns.visible.value"
                  :key="column.key"
                  :data-list-column="column.key"
                  :title="column.label"
                >
                  <span class="catalog-list-cell">{{ column.label }}</span>
                </th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in list?.items"
                :key="row.id"
                :class="{
                  'catalog-returned-row':
                    row.id === listReturn.returnedId.value,
                }"
              >
                <td
                  v-for="column in listColumns.visible.value"
                  :key="column.key"
                  :data-list-column="column.key"
                  :class="{ 'catalog-cell-wrap': column.wrap }"
                  :title="listCell(row, column.key)"
                >
                  <button
                    v-if="column.key === 'name'"
                    class="master-text-button"
                    :data-catalog-record="row.id"
                    :disabled="opening"
                    @keydown="listReturn.keys"
                    @click="open(row.id)"
                  >
                    {{ listCell(row, column.key) }}
                  </button>
                  <span v-else class="catalog-list-cell">{{
                    listCell(row, column.key)
                  }}</span>
                </td>
                <td>
                  <button class="master-text-button" @click="open(row.id)">
                    资料详情
                  </button>
                </td>
              </tr>
              <tr v-if="!list?.items.length">
                <td
                  :colspan="listColumns.visible.value.length + 1"
                  class="master-empty-tab"
                >
                  此范围暂无商品资料
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="master-pagination">
          <span
            >每行对应一个商品 / 服务身份，证件和组织多项关联不重复计数。</span
          ><el-button
            :disabled="!offset || loading || opening"
            @click="
              offset -= 50;
              load();
            "
            >上一页</el-button
          ><span>{{ Math.floor(offset / 50) + 1 }} 页</span
          ><el-button
            :disabled="!list?.has_more || loading || opening"
            @click="
              offset += 50;
              load();
            "
            >下一页</el-button
          >
        </div>
      </div>
      <div v-else class="master-editor" @keydown="catalogSaveKey($event, save)">
        <div class="master-toolbar">
          <div>
            <h2>
              {{ displayedValues.name || "新建商品 / 项目" }}
              <span class="master-state"
                >{{ record ? labels[record.approval_state] : "未保存"
                }}{{
                  savedRefreshId
                    ? " · 已保存，待读取"
                    : dirty
                      ? " · 已修改"
                      : ""
                }}</span
              >
            </h2>
            <small
              >{{ path.join(" / ") }} · 表单 v{{ pinnedRevision
              }}<template v-if="record">
                · 数据 v{{ record.data_version }}</template
              ></small
            >
          </div>
          <div>
            <el-button :disabled="busy || opening" @click="close"
              >返回查询</el-button
            ><el-button v-if="editMode" @click="reloadSchema"
              >重载规则并校验</el-button
            ><el-button v-if="editMode" @click="validate"
              >校验全部页签</el-button
            >
          </div>
        </div>
        <nav class="master-tabs" aria-label="商品资料分组">
          <button
            v-for="t in schema.payload.tabs"
            :key="t.id"
            :class="{ active: tab === t.id }"
            @click="tab = t.id"
          >
            {{ t.label
            }}<b v-if="issues.filter((i) => i.tab === t.id).length">{{
              issues.filter((i) => i.tab === t.id).length
            }}</b>
          </button>
        </nav>
        <p
          v-if="savedRefreshId && !opening"
          class="master-issue-strip"
          role="status"
        >
          资料已保存，最新详情暂未读回。请点击“重试读取已保存资料”；不会重复建档。
        </p>
        <p v-if="record && !editMode" class="master-issue-strip" role="status">
          {{
            record.has_changes
              ? "当前正式资料 · 有待审核变更，审核通过后统一生效"
              : "只读查阅 · " + labels[record.approval_state]
          }}
          · 编码 {{ displayedValues.code }}
        </p>
        <div v-if="editMode" class="master-issue-strip" role="status">
          <template v-if="issues.length"
            ><span>{{ issues.length }} 项需要处理：</span
            ><button
              v-for="i in issues"
              :key="i.field + i.message"
              @click="focusIssue(i)"
            >
              {{ i.message }}
            </button></template
          ><template v-else
            >字段位置跨品类保持一致；不适用字段保留位置。保存草稿允许待补资料，正式审核校验全部页签。</template
          >
        </div>
        <MasterFields
          v-for="group in schema.payload.tabs"
          :key="group.id"
          v-show="tab === group.id"
          :data="schema"
          :profile="profile"
          :tab="group.id"
          :values="displayedValues"
          :read-view="!editMode"
          :issues="issues"
          :existing="!!record"
          :unit-locked="!!record?.skus.some((s) => !!s.base_unit)"
          :organizations="organizations"
          :disabled="busy || opening || !!savedRefreshId"
          @change="(key, value) => (values[key] = value)"
          @relation="openRelation"
        >
          <template v-if="singleUnit" #barcodes="field">
            <input
              :id="field.id"
              v-model="barcode"
              :disabled="field.disabled"
              aria-label="条码（基本单位）"
              maxlength="128"
              autocomplete="off"
              :placeholder="`填写每${values.base_unit || '基本单位'}包装上的条码`"
            />
          </template>
          <template v-if="singleUnit" #reference_prices="field">
            <fieldset
              :id="field.id"
              class="master-inline-prices"
              :disabled="field.disabled"
            >
              <p>
                按基本单位填写 · 人民币含税 · 元／{{
                  values.base_unit || "基本单位"
                }}。留空为未设置，0为零价。
              </p>
              <div class="master-price-inputs">
                <label v-for="(name, key) in priceLabels" :key="key"
                  >{{ name
                  }}<input
                    v-model="prices[key]"
                    :aria-label="name"
                    inputmode="decimal"
                    placeholder="未设置"
                    maxlength="19"
                /></label>
              </div>
              <label class="master-price-note"
                >价格备注<input
                  v-model="prices.note"
                  aria-label="价格备注"
                  maxlength="500"
                  placeholder="选填，例如适用条件"
              /></label>
              <small
                >随商品资料一起保存；参考价不改写单据成交价和库存成本。</small
              >
            </fieldset>
          </template>
        </MasterFields>
        <div
          v-if="editMode && tab === 'basic' && profile !== 'service'"
          class="master-packaging-entry"
        >
          <div>
            <strong>其他包装</strong
            ><span
              >基本单位：{{
                values.base_unit || "请先填写"
              }}。中包装、大包装分别维护，例如 1箱＝100盒。</span
            >
            <small v-if="baseBarcodes(tradeOriginal).length > 1"
              >另有
              {{ baseBarcodes(tradeOriginal).length - 1 }}
              条基本单位条码保留。上方展示最近登记的一条；清空只停用当前这条。</small
            >
            <small v-if="!record">先保存商品资料，再添加其他包装。</small>
          </div>
          <el-button
            :disabled="!record || busy || opening || !!savedRefreshId"
            @click="openRelation('packaging')"
            >维护其他包装</el-button
          >
          <el-button
            :disabled="!record || busy || opening || !!savedRefreshId"
            @click="openRelation('barcodes')"
            >其他条码与历史</el-button
          >
        </div>
        <MasterChangeSummary
          v-if="record && editMode"
          kind="product"
          :id="record.id"
          :version="record.version"
          @failure="emit('failure', $event)"
          @saved="open(record!.id, true)"
        />
        <footer v-if="editMode" class="master-footer">
          <span
            >编码：{{ values.code || "保存时生成"
            }}<small>Ctrl / ⌘ + S 保存变更 · 证件随商品一次审核</small></span
          ><input
            v-model="reason"
            aria-label="主数据修订说明"
            placeholder="修订说明"
            maxlength="200"
          /><el-button
            v-if="can('catalog.write')"
            :disabled="busy || opening || relationDirty"
            @click="saveAndReturn"
            >保存并返回列表</el-button
          ><el-button
            type="primary"
            v-if="can('catalog.write')"
            :loading="busy"
            :disabled="opening"
            @click="save"
            >{{
              savedRefreshId && !opening ? "重试读取已保存资料" : "保存资料草稿"
            }}</el-button
          >
          <el-button :disabled="busy || opening" @click="cancelRevision">{{
            dirty ? "取消修订" : "返回只读查阅"
          }}</el-button>
        </footer>
      </div>
    </div>
    <el-dialog
      v-model="chooseCategory"
      title="明确选择建档品类"
      width="560px"
      :close-on-click-modal="false"
      ><p>分组仅用于汇总。请选择当前分支内的具体品类。</p>
      <select v-model="newNode" aria-label="新建商品品类">
        <option value="">请选择</option>
        <option v-for="n in options" :key="n.node_id" :value="n.node_id">
          {{ nodePath(schema.payload, n.node_id).slice(1).join(" / ") }}
        </option></select
      ><template #footer
        ><el-button :disabled="!newNode" type="primary" @click="start(newNode)"
          >进入建档</el-button
        ></template
      ></el-dialog
    >
    <el-dialog
      :model-value="!!relation"
      @update:model-value="
        (v: boolean) => {
          if (!v) {
            relation = '';
            childDirty = false;
          }
        }
      "
      :title="
        relation === 'packaging'
          ? '其他包装与换算'
          : relation === 'barcodes'
            ? '其他条码与历史'
            : relation === 'reference_prices'
              ? '商品参考价格'
              : schema.payload.fields.find((f) => f.key === relation)
                  ?.default_label
      "
      width="min(1060px,95vw)"
      :close-on-click-modal="false"
      :before-close="closeRelation"
      destroy-on-close
    >
      <template
        v-if="
          ['manufacturer', 'service_provider', 'regulatory_subject'].includes(
            relation,
          )
        "
        ><p>
          选择真实组织身份并明确其角色；同一组织可承担多个角色，名称相同不会自动合并。
        </p>
        <div class="master-search">
          <input
            v-model="orgSearch"
            placeholder="检索组织名称 / 标识"
            @keyup.enter="loadOrganizations"
          /><el-button @click="loadOrganizations">查询组织</el-button
          ><el-button
            @click="
              roleDraft.push({
                organization_id: '',
                role_code: roleOptions[0],
                evidence_ref: '',
              });
              childDirty = true;
            "
            >添加关系</el-button
          >
        </div>
        <div
          v-for="(r, index) in roleDraft"
          :key="index"
          class="master-relation-row"
        >
          <select
            v-model="r.organization_id"
            aria-label="组织身份"
            @change="childDirty = true"
          >
            <option value="">请选择组织</option>
            <option
              v-for="o in organizations.filter(
                (o) => o.kind === 'ORGANIZATION',
              )"
              :key="o.id"
              :value="o.id"
            >
              {{ o.name }} · {{ o.legal_code || o.id.slice(0, 8) }}
            </option></select
          ><select
            v-model="r.role_code"
            aria-label="实际角色"
            @change="childDirty = true"
          >
            <option v-for="role in roleOptions" :key="role" :value="role">
              {{ schema.role_labels[role] }}
            </option></select
          ><input
            v-model="r.evidence_ref"
            placeholder="原件凭据编号（可选）"
            maxlength="120"
            @input="childDirty = true"
          /><el-button
            @click="
              roleDraft.splice(index, 1);
              childDirty = true;
            "
            >移除关系</el-button
          >
        </div>
        <details class="master-history">
          <summary>新建独立组织 / 科室</summary>
          <div class="master-search">
            <input v-model="newOrg.name" placeholder="组织或科室全称" /><input
              v-model="newOrg.legal_code"
              placeholder="统一社会信用代码 / 主体标识"
            /><select v-model="newOrg.kind">
              <option value="ORGANIZATION">组织机构</option>
              <option value="DEPARTMENT">门诊科室</option></select
            ><select
              v-if="newOrg.kind === 'DEPARTMENT'"
              v-model="newOrg.parent_id"
            >
              <option :value="null">选择所属机构</option>
              <option
                v-for="o in organizations.filter(
                  (o) => o.kind === 'ORGANIZATION',
                )"
                :key="o.id"
                :value="o.id"
              >
                {{ o.name }}
              </option></select
            ><el-button
              :disabled="!newOrg.name || !can('catalog.write')"
              :loading="busy"
              @click="createOrg"
              >创建组织身份</el-button
            >
          </div>
        </details>
        <el-button
          type="primary"
          :disabled="!can('catalog.write')"
          @click="useRelations"
          >应用到当前草稿</el-button
        ></template
      >
      <template v-else-if="relation === 'product_certificates'"
        ><p>
          为当前商品明确关联适用证件版本；同一证件可关联多个商品，不按最新更新时间自动替换。
        </p>
        <div class="master-search">
          <input
            v-model="certSearch"
            placeholder="检索证件编号 / 持有人"
            @keyup.enter="loadCertificates"
          /><el-button @click="loadCertificates">查询证件</el-button>
        </div>
        <div
          v-for="c in certificates"
          :key="c.id"
          class="master-certificate-choice"
        >
          <label
            ><input
              type="checkbox"
              :checked="certificateDraft.some((x) => x.certificate_id === c.id)"
              @change="
                (e) => {
                  certificateDraft = certificateDraft.filter(
                    (x) => x.certificate_id !== c.id,
                  );
                  if ((e.target as HTMLInputElement).checked)
                    certificateDraft.push({
                      certificate_id: c.id,
                      certificate_version: Number(c.version),
                    });
                  childDirty = true;
                }
              "
            />{{ c.number }} · {{ c.holder_name }} · v{{ c.version }} ·
            {{ c.state === "VERIFIED" ? "已核验" : "随主体待审核" }}</label
          >
          <label style="margin-left: 18px">
            <input
              type="radio"
              name="business-certificate"
              :disabled="
                !certificateDraft.some((x) => x.certificate_id === c.id)
              "
              :checked="
                certificateDraft.some(
                  (x) => x.certificate_id === c.id && x.business_use,
                )
              "
              @change="
                certificateDraft.forEach(
                  (x) => (x.business_use = x.certificate_id === c.id),
                );
                childDirty = true;
              "
            />
            本商品业务适用证件
          </label>
        </div>
        <el-button
          type="primary"
          :disabled="!can('catalog.write')"
          @click="useRelations"
          >应用证件关联</el-button
        >
        <details v-if="record" class="master-history">
          <summary>维护证件原件 / 续期（随商品审核）</summary>
          <ProductCertificates
            :product="record.id"
            active
            @failure="emit('failure', $event)"
            @editing="nestedDirty.child0 = $event"
            @saved="certificateSaved"
          />
        </details>
        <p v-else>新建商品请先保存资料草稿，再维护证件原件。</p></template
      >
      <template v-else-if="record"
        ><template v-if="relation === 'operational_settings'"
          ><ProductEntitySettings
            :product="record.id"
            @failure="emit('failure', $event)"
            @editing="nestedDirty.child1 = $event" />
          <details class="master-history">
            <summary>维护企业通用经营默认值（保留原功能）</summary>
            <CatalogOperations
              hide-search-alias
              kind="products"
              :target="record.id"
              active
              :master-version="String(record.version)"
              @failure="emit('failure', $event)"
              @editing="nestedDirty.child2 = $event"
            /></details></template
        ><template v-else-if="record.skus.length"
          ><section v-for="sku in record.skus" :key="sku.id">
            <h4>商品编码 {{ sku.code }} · 基本单位 {{ sku.base_unit }}</h4>
            <CatalogPrices
              v-if="relation === 'reference_prices'"
              :sku="sku.id"
              :code="sku.code"
              active
              @failure="emit('failure', $event)"
              @editing="nestedDirty.child3 = $event"
            /><template v-else-if="relation === 'packaging'"
              ><PackagingPanel
                :sku="sku.id"
                expanded
                @failure="emit('failure', $event)"
                @editing="nestedDirty.child4 = $event"
              />
              <p class="form-note">
                包装审核启用后，可在“其他条码与历史”中登记该包装条码。
              </p> </template
            ><template v-else-if="relation === 'barcodes'"
              ><CatalogCodes
                kind="skus"
                :target="sku.id"
                :base-unit="sku.base_unit"
                :schemes="['BARCODE']"
                @failure="emit('failure', $event)"
                @editing="nestedDirty.child5 = $event" /></template
            ><CatalogCodes
              v-else
              kind="skus"
              :target="sku.id"
              :base-unit="sku.base_unit"
              :category="
                profile === 'drug'
                  ? 'DRUG'
                  : profile === 'device'
                    ? 'DEVICE'
                    : profile.toUpperCase()
              "
              :schemes="[
                'NHSA_DRUG',
                'NHSA_CONSUMABLE',
                'NHSA_IVD',
                'UDI_DI',
                'MEITUAN',
                'ELEME',
                'TAOBAO',
                'WANGDIANTONG',
                'CUSTOM',
              ]"
              @failure="emit('failure', $event)"
              @editing="nestedDirty.child6 = $event"
            /></section
        ></template>
        <p v-else>
          门诊服务不创建库存 SKU；机构服务对接编码需在后续服务接口中配置。
        </p></template
      >
      <p v-else>请先保存商品草稿，再维护关联价格、包装或平台编码。</p>
    </el-dialog>
    <el-dialog
      v-model="importOpen"
      title="导入主数据草稿"
      width="760px"
      :close-on-click-modal="false"
      ><p>
        同一批次明确一个品类。采用 UTF-8 CSV，最多 100
        行；空值不伪造证照和组织角色。
      </p>
      <select
        v-model="newNode"
        aria-label="导入品类"
        @change="importResult = null"
      >
        <option value="">请选择明确品类</option>
        <option
          v-for="n in schema.payload.nodes.filter(
            (n) => n.enabled && n.is_assignable,
          )"
          :key="n.node_id"
          :value="n.node_id"
        >
          {{ nodePath(schema.payload, n.node_id).slice(1).join(" / ") }}
        </option></select
      ><el-button @click="template">下载字段键模板</el-button
      ><input type="file" accept=".csv" @change="readFile" /><textarea
        v-model="csv"
        rows="9"
        aria-label="CSV 导入内容"
        @input="importResult = null"
      />
      <p v-if="importResult">
        {{ importResult.valid ? "校验通过" : "校验失败" }} ·
        {{ importResult.count }} 行
      </p>
      <ul v-if="importResult?.errors.length">
        <li v-for="(e, i) in importResult.errors" :key="i">
          第 {{ e.row }} 行：{{ e.message }}
        </li>
      </ul>
      <template #footer
        ><el-button :disabled="!newNode || !csv" @click="previewImport"
          >预览校验</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="!importResult?.valid"
          @click="submitImport"
          >导入草稿</el-button
        ></template
      ></el-dialog
    >
    <el-dialog
      :model-value="leaveOpen"
      title="离开当前商品"
      width="480px"
      :close-on-click-modal="false"
      :show-close="false"
      :close-on-press-escape="false"
    >
      <p>当前商品还有未保存的修改。保存成功后才能继续离开。</p>
      <p v-if="relationDirty">
        关联窗口中也有未保存内容，请先取消离开，完成该窗口的保存或应用。
      </p>
      <template #footer>
        <el-button :disabled="busy" @click="leaveChoice('cancel')"
          >取消</el-button
        >
        <el-button :disabled="busy" @click="leaveChoice('discard')"
          >放弃修改</el-button
        >
        <el-button
          type="primary"
          :loading="busy"
          :disabled="relationDirty"
          @click="leaveChoice('save')"
          >保存后离开</el-button
        >
      </template>
    </el-dialog>
    <el-drawer
      v-model="historyListOpen"
      title="变更记录"
      size="min(640px, 96vw)"
      append-to-body
    >
      <p class="data-task-heading">
        {{ record?.active_values.name }} · {{ record?.active_values.code }}
      </p>
      <template v-if="historyOpen"
        ><el-button @click="historyOpen = false">返回版本列表</el-button>
        <table class="master-list-table">
          <tbody>
            <tr v-for="(v, k) in historyValues" :key="k">
              <td>
                {{ historyLabels[k] || k }}
              </td>
              <td>{{ summary(v) }}</td>
            </tr>
          </tbody>
        </table></template
      >
      <template v-else>
        <MasterChangeSummary
          readonly
          v-if="record"
          kind="product"
          :id="record.id"
          :version="record.version"
          @failure="emit('failure', $event)"
          @saved="open(record!.id, true)"
        />
        <p v-if="!record?.history.length">暂无历史版本</p>
        <p v-for="h in record?.history" :key="h.data_version">
          <el-button @click="history(h.data_version)"
            >数据 v{{ h.data_version }} / 表单 v{{ h.form_revision }}</el-button
          ><small> {{ h.created_at }}</small>
        </p>
        <details v-if="record && Object.values(record.legacy).some(Boolean)">
          <summary>旧档原始记录</summary>
          <p>{{ record.legacy.message }}</p>
          <dl>
            <template v-for="(v, k) in record.legacy" :key="k"
              ><dt>{{ legacyLabels[k] || k }}</dt>
              <dd>{{ v || "未记载" }}</dd></template
            >
          </dl>
        </details>
      </template>
    </el-drawer>
    <el-drawer
      v-model="attachmentsOpen"
      title="证照附件"
      size="min(640px, 96vw)"
      append-to-body
      destroy-on-close
      ><CatalogAttachments
        v-if="record"
        readonly
        area="products"
        :parent="record.id"
        :model-value="null"
        :selectable="false"
        :disabled="false"
        @failure="emit('failure', $event)"
    /></el-drawer>
    <el-dialog
      v-model="reviewOpen"
      title="商品资料审核"
      width="520px"
      :close-on-click-modal="false"
      ><p>
        正式审核会检查全部页签、组织关系和明确关联的证件版本；建档及修订人员不能审核自身资料。
      </p>
      <select v-model="reviewDecision">
        <option value="APPROVE">审核通过</option>
        <option value="SUSPEND">停用</option></select
      ><input
        v-model="reviewReason"
        aria-label="审核说明"
        placeholder="审核说明"
      /><template #footer
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="!reviewReason"
          @click="review"
          >确认处理</el-button
        ></template
      ></el-dialog
    >
  </MasterDataLayout>
  <el-skeleton v-else :rows="10" animated />
  <CatalogColumnSettings
    v-if="columnSettings"
    :key="listColumns.storageKey.value"
    title="商品列表列设置"
    :columns="listColumns.columns.value"
    :definitions="columnDefinitions"
    :save="listColumns.save"
    @close="columnSettings = false"
  />
  <InformationCard
    v-if="record"
    v-model="infoOpen"
    kind="product"
    :record="record.active_values"
    :legacy="record.legacy"
    :version="record.data_version"
    :state="record.approval_state"
  />
</template>
<style scoped>
.configurable-master-list {
  table-layout: fixed;
}
.catalog-list-cell,
.configurable-master-list .master-text-button[data-catalog-record] {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.configurable-master-list .catalog-cell-wrap .catalog-list-cell,
.configurable-master-list
  .catalog-cell-wrap
  .master-text-button[data-catalog-record] {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.master-packaging-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 20px 16px;
  padding: 14px 16px;
  background: var(--ui-surface-soft, #f4f8fb);
  border: 1px solid var(--ui-border, #dce5ef);
  border-radius: 6px;
}
.master-packaging-entry > div {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 5px;
}
.master-packaging-entry span,
.master-packaging-entry small {
  color: var(--ui-muted, #667a8d);
  font-size: 12px;
}
.master-inline-prices {
  border: 0;
  padding: 12px 14px;
  margin: 0;
  min-width: 0;
  background: var(--ui-surface-soft, #f4f8fb);
  border-radius: 6px;
}
.master-inline-prices p {
  margin: 0 0 12px;
  font-size: 13px;
}
.master-price-inputs {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.master-price-inputs label,
.master-price-note {
  display: grid;
  gap: 5px;
  white-space: normal;
}
.master-price-note {
  margin-top: 12px;
}
.master-inline-prices small {
  display: block;
  margin-top: 10px;
  color: var(--ui-muted, #667a8d);
}
@media (max-width: 600px) {
  .master-packaging-entry {
    flex-wrap: wrap;
  }
  .master-packaging-entry > div {
    flex-basis: 100%;
  }
  .master-price-inputs {
    grid-template-columns: 1fr;
  }
}
</style>
