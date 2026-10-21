<script setup lang="ts">
import type { CatalogDictionary } from "./catalogDictionaries";
import {
  unallocatedReceipt,
  receiptAmountIssue,
  inspectionAmountIssue,
  inspectionDictionary,
} from "./purchaseEntry";
import DateInput from "./DateInput.vue";
import AdmissionWarnings from "./AdmissionWarnings.vue";
import DocumentLineGrid from "./DocumentLineGrid.vue";
import { documentCell, type DocumentValues } from "./documentColumns";
import { businessToday } from "./dates";
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { createEditor } from "./workspace";
import { purchaseTask, purchaseChanged } from "./purchaseWorkflow";
import PurchaseHistory from "./PurchaseHistory.vue";
import PurchaseRejectedHandover from "./PurchaseRejectedHandover.vue";
import type { PackageSnapshot } from "./business";
import {
  computed,
  defineAsyncComponent,
  nextTick,
  onMounted,
  ref,
  watch,
} from "vue";
import { request, session } from "./api";
import { localTime } from "./sales";
import {
  can,
  labels,
  positive,
  quantity,
  useCommand,
  type PurchaseOptions,
} from "./business";
const ReturnsPage = defineAsyncComponent(() => import("./ReturnsPage.vue"));
const returnSource = ref("");
const props = defineProps<{ id: string; options: PurchaseOptions }>();
const emit = defineEmits<{
  identity: [number: string];
  failure: [error: unknown];
  back: [];
  sale: [source: { id: string; entity_id: string }];
}>();
interface ReceiptLine extends DocumentValues {
  id: string;
  order_line_id: string;
  name: string;
  lot_no: string;
  raw_expiry: string;
  qty: string;
  pending_qty: string;
  qualified_qty: string;
  rejected_qty: string;
  posted_qty: string;
  custody_qty: string;
  base_unit: string;
  rejected_returned_qty: string;
  rejected_pending_qty: string;
}
interface QualifiedLine extends DocumentValues {
  name: string;
  lot_no: string;
  qualified_qty: string;
  base_unit: string;
}
interface Segment {
  posting_id: string | null;
  lines: QualifiedLine[];
  id: string;
  inspection_id: string;
  version: string;
  state: string;
  note: string;
}
interface Receipt {
  rejected_handovers: {
    id: string;
    receipt_line_id: string;
    name: string;
    lot_no: string;
    qty: string;
    base_unit: string;
    handover_ref: string;
    recipient: string;
    reason: string;
    created_at: string;
  }[];
  inspections: (QualifiedLine & {
    id: string;
    inspected_at: string;
    rejected_qty: string;
    note: string;
  })[];
  id: string;
  number: string;
  version: string;
  created_by: string;
  shipment_ref: string;
  transport_note: string;
  lines: ReceiptLine[];
  postings: Segment[];
}
interface Detail {
  order: {
    id: string;
    number: string;
    entity_id: string;
    revision_no: number;
    revision_editors: string[];
    version: string;
    created_by: string;
    approval_state: string;
    fulfillment_state: string;
    warehouse_id: string;
    supplier_id: string;
    supplier_name: string;
    warehouse_name: string;
    closed_at: string | null;
    closed_by: string | null;
    close_reason: string | null;
    source_sales_order_id: string | null;
    source_sales_number: string | null;
    source_sales_version: number | null;
  };
  lines: (DocumentValues & {
    sku_id: string;
    qty_scale: number;
    trade_qty: string | null;
    package_snapshot: PackageSnapshot | null;
    id: string;
    name: string;
    base_unit: string;
    qty: string;
    remaining_qty: string;
    received_qty: string;
    closed_qty: string;
    unit_price: string;
    tax_rate: string;
  })[];
  receipts: Receipt[];
}
const doc = ref<Detail | null>(null),
  loading = ref(false),
  dialog = ref("");
const taskNote = ref("");
const detailRoot = ref<HTMLElement>();
let alive = true;
disposeGuard(() => {
  alive = false;
});
const revising = ref(false);
async function revise() {
  const record = doc.value;
  if (!record) return;
  revising.value = true;
  try {
    await createEditor(
      "purchase",
      record.order.entity_id,
      {
        warehouse_id: record.order.warehouse_id,
        supplier_id: record.order.supplier_id,
        lines: record.lines.map((item) => ({
          client_key: crypto.randomUUID(),
          sku_id: item.sku_id,
          packaging_id: item.package_snapshot?.packaging_id || "",
          qty: item.trade_qty || item.qty,
          unit_price: item.unit_price,
          tax_rate: String(Number(item.tax_rate)),
        })),
      },
      { id: record.order.id, version: record.order.version },
      record.order.number + " 修改",
    );
  } catch (e) {
    emit("failure", e);
  } finally {
    revising.value = false;
  }
}
const selfReview = computed(
  () =>
    !!doc.value &&
    (doc.value.order.created_by === session.value?.principal_id ||
      doc.value.order.revision_editors.includes(
        session.value?.principal_id || "",
      )),
);
const reason = ref(""),
  inspectionReceipt = ref<Receipt | null>(null),
  posting = ref<Segment | null>(null);
const receiptLine = () => ({
  client_key: crypto.randomUUID(),
  order_line_id: "",
  location_id: "",
  qty: "",
  lot_no: "",
  mfg_date: "",
  expiry_date: "",
});
const receiptForm = ref({
  shipment_ref: "",
  transport_note: "",
  lines: [receiptLine()],
});
const inspectionForm = ref({
  note: "",
  lines: [] as {
    receipt_line_id: string;
    name: string;
    lot: string;
    pending: string;
    qty_scale: number;
    base_unit: string;
    qualified_qty: string;
    rejected_qty: string;
  }[],
});
const receiptEditor = ref<HTMLFormElement>(),
  inspectionEditor = ref<HTMLFormElement>();
const entryError = ref("");
const reviewDictionaries = ref<CatalogDictionary[]>([]),
  reviewLoading = ref(false),
  reviewError = ref("");
const approvalConclusion = ref(""),
  inspectionConclusion = ref("");
const inspectionKey = computed(() =>
  inspectionDictionary(inspectionForm.value.lines),
);
const reviewItems = (key: string) =>
  reviewDictionaries.value
    .find((d) => d.key === key)
    ?.items.filter((i) => i.active) || [];
let reviewEpoch = 0;
async function loadReviewOptions() {
  const epoch = ++reviewEpoch;
  reviewLoading.value = true;
  reviewError.value = "";
  reviewDictionaries.value = [];
  try {
    const result = await request<{ items: CatalogDictionary[] }>(
      "/api/v1/purchasing/review-options",
    );
    if (!alive || epoch !== reviewEpoch) return;
    reviewDictionaries.value = result.items;
    approvalConclusion.value =
      reviewItems("purchase_approval").find((i) => i.is_default)?.value || "";
  } catch (e) {
    if (epoch === reviewEpoch)
      reviewError.value = e instanceof Error ? e.message : "结论字典读取失败";
  } finally {
    if (epoch === reviewEpoch) reviewLoading.value = false;
  }
}
watch([inspectionKey, reviewDictionaries], () => {
  const items = reviewItems(inspectionKey.value);
  if (!items.some((i) => i.value === inspectionConclusion.value))
    inspectionConclusion.value = items.find((i) => i.is_default)?.value || "";
});
watch(dialog, () => {
  entryError.value = "";
});
function showEntryIssue(
  form: HTMLFormElement | undefined,
  issue: { index: number; message: string },
  key: string,
) {
  entryError.value = `第 ${issue.index + 1} 行：${issue.message}`;
  const input = form
    ?.querySelectorAll<HTMLTableRowElement>("tbody tr")
    [issue.index]?.querySelector<HTMLInputElement>(
      `[data-grid-cell="${key}"] input`,
    );
  input?.focus();
  input?.scrollIntoView({ block: "nearest", inline: "nearest" });
}
function formEnter(event: KeyboardEvent) {
  if (
    event.key === "Enter" &&
    (event.ctrlKey || event.metaKey) &&
    !event.isComposing
  ) {
    event.preventDefault();
    if (!busy.value)
      (event.target as HTMLElement).closest("form")?.requestSubmit();
    return;
  }
  if (
    event.defaultPrevented ||
    event.isComposing ||
    event.key !== "Enter" ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  )
    return;
  const input = event.target as HTMLElement;
  if (input.closest(".document-line-grid") || !input.matches("input,select"))
    return;
  const form = input.closest("form");
  const controls = [
    ...(form?.querySelectorAll<HTMLElement>("input,select,textarea,button") ||
      []),
  ].filter(
    (el) =>
      el.tabIndex >= 0 &&
      !el.matches(":disabled") &&
      el.getClientRects().length &&
      (!el.closest(".document-line-grid") ||
        !!el.closest('[data-stop="true"]')),
  );
  const index = controls.indexOf(input);
  const next = controls[index + (event.shiftKey ? -1 : 1)];
  event.preventDefault();
  next?.focus();
}
const { busy, execute } = useCommand((error) => emit("failure", error));
const locations = computed(() =>
  props.options.locations.filter(
    (l) => l.warehouse_id === doc.value?.order.warehouse_id,
  ),
);
const remaining = computed(
  () => doc.value?.lines.filter((l) => positive(l.remaining_qty)) || [],
);
const receiptOrderLine = (id: string) =>
  doc.value?.lines.find((line) => line.id === id);
function receiptUnitNote(id: string) {
  const line = receiptOrderLine(id);
  if (!line) return "";
  const pack = line.package_snapshot;
  const ordered = `${quantity(line.trade_qty || line.qty)} ${pack?.unit_name || line.base_unit}`;
  const conversion = pack?.packaging_id
    ? `，折合 ${quantity(line.qty)} ${line.base_unit}（${pack.denominator} ${pack.unit_name} ＝ ${pack.numerator} ${line.base_unit}）`
    : "";
  return `本行采购 ${ordered}${conversion}；到货数量按 ${line.base_unit} 填写。`;
}
function inspectionGroups(receipt: Receipt) {
  const groups = new Map<string, Receipt["inspections"]>();
  for (const row of receipt.inspections) {
    const list = groups.get(row.id) || [];
    list.push(row);
    groups.set(row.id, list);
  }
  return [...groups].map(([id, rows]) => ({ id, rows }));
}
const receiptRows = computed(() =>
  receiptForm.value.lines.map((line) => {
    const source = receiptOrderLine(line.order_line_id);
    return {
      row_key: line.client_key,
      document_values: {
        ...source?.document_values,
        qty: line.qty,
        lot_no: line.lot_no,
        mfg_date: line.mfg_date,
        expiry_date: line.expiry_date,
        location: locations.value.find((l) => l.id === line.location_id)?.code,
      },
      document_status: source?.document_status,
    };
  }),
);
const inspectionRows = computed(() =>
  inspectionForm.value.lines.map((line) => {
    const source = inspectionReceipt.value?.lines.find(
      (l) => l.id === line.receipt_line_id,
    );
    return {
      row_key: line.receipt_line_id,
      document_values: {
        ...source?.document_values,
        qualified_qty: line.qualified_qty,
        rejected_qty: line.rejected_qty,
      },
      document_status: source?.document_status,
    };
  }),
);
async function load() {
  loading.value = true;
  try {
    doc.value = await request<Detail>(`/api/v1/purchase-orders/${props.id}`);
    if (!alive) return;
    emit("identity", doc.value.order.number);
    await followTask();
  } catch (error) {
    emit("failure", error);
  } finally {
    loading.value = false;
  }
}
async function done() {
  dialog.value = "";
  await load();
  purchaseChanged();
}
async function followTask() {
  const target = purchaseTask.value;
  if (
    !target ||
    target.item.order_id !== props.id ||
    !doc.value ||
    dialog.value ||
    target.entity !== doc.value.order.entity_id
  )
    return;
  await nextTick();
  // Navigation may remount an existing record tab. Only its surviving instance
  // may consume the requested action; never replace an unfinished form.
  if (!alive || purchaseTask.value !== target) return;
  purchaseTask.value = null;
  const item = target.item;
  const receipt = doc.value.receipts.find((r) => r.id === item.receipt_id);
  if (receipt) {
    const card = detailRoot.value?.querySelector<HTMLElement>(
      `[data-purchase-receipt="${receipt.id}"]`,
    );
    const history = card?.querySelector<HTMLDetailsElement>(
      ".inspection-history",
    );
    if (item.inspection_id && history) history.open = true;
    const destination = item.segment_id
      ? card?.querySelector<HTMLElement>(
          `[data-purchase-segment="${item.segment_id}"]`,
        )
      : item.inspection_id
        ? card?.querySelector<HTMLElement>(
            `[data-purchase-inspection="${item.inspection_id}"]`,
          )
        : card;
    destination?.scrollIntoView({ block: "center" });
    destination?.focus({ preventScroll: true });
  }
  if (!target.action) return;
  taskNote.value = "";
  if (
    target.action === "receive" &&
    can("purchase.receive") &&
    doc.value.order.approval_state === "APPROVED" &&
    doc.value.order.fulfillment_state === "OPEN" &&
    remaining.value.length
  )
    receive();
  else if (
    target.action === "inspect" &&
    can("purchase.inspect") &&
    receipt &&
    receipt.created_by !== session.value?.principal_id &&
    receipt.lines.some((l) => positive(l.pending_qty))
  )
    inspect(receipt);
  else if (target.action === "post" && can("purchase.post")) {
    const segment = receipt?.postings.find(
      (p) => p.id === item.segment_id && p.state === "APPROVED",
    );
    if (segment) {
      posting.value = segment;
      dialog.value = "post";
    } else taskNote.value = "这笔合格结果已变化，请核对下方最新记录。";
  } else taskNote.value = "来源状态或办理权限已变化，请核对最新记录后继续。";
}
function transition(action: string) {
  reason.value = "";
  dialog.value = action;
  if (action === "approve") {
    approvalConclusion.value = "";
    void loadReviewOptions();
  }
}
async function saveTransition() {
  if (
    !doc.value ||
    (dialog.value === "approve" &&
      (reviewLoading.value || !approvalConclusion.value))
  )
    return;
  await execute(
    `/api/v1/purchase-orders/${props.id}/${dialog.value}`,
    {
      expected_version: doc.value.order.version,
      reason: reason.value,
      ...(dialog.value === "approve"
        ? { conclusion: approvalConclusion.value }
        : {}),
    },
    done,
  );
}
function addReceiptLine() {
  const source =
    remaining.value.find((l) =>
      positive(
        unallocatedReceipt(l.id, remaining.value, receiptForm.value.lines),
      ),
    ) || remaining.value[0];
  receiptForm.value.lines.push({
    ...receiptLine(),
    order_line_id: source?.id || "",
    location_id: locations.value[0]?.id || "",
    qty: source
      ? unallocatedReceipt(source.id, remaining.value, receiptForm.value.lines)
      : "",
  });
}
function changeReceiptProduct(index: number) {
  const line = receiptForm.value.lines[index]!;
  line.qty = unallocatedReceipt(
    line.order_line_id,
    remaining.value,
    receiptForm.value.lines,
    index,
  );
  line.lot_no = line.mfg_date = line.expiry_date = "";
}
function receive() {
  receiptForm.value = {
    shipment_ref: "",
    transport_note: "",
    lines: remaining.value.map((l) => ({
      ...receiptLine(),
      order_line_id: l.id,
      location_id: locations.value[0]?.id || "",
      qty: quantity(l.remaining_qty),
    })),
  };
  dialog.value = "receive";
}
async function saveReceipt() {
  if (!doc.value) return;
  const issue = receiptAmountIssue(remaining.value, receiptForm.value.lines);
  if (issue) {
    showEntryIssue(receiptEditor.value, issue, "qty");
    return;
  }
  entryError.value = "";
  await execute(
    `/api/v1/purchase-orders/${props.id}/receipts`,
    {
      expected_version: doc.value.order.version,
      ...receiptForm.value,
      lines: receiptForm.value.lines
        .filter((l) => positive(l.qty))
        .map(({ client_key: _key, ...line }) => line),
    },
    done,
  );
}
function inspect(receipt: Receipt) {
  inspectionReceipt.value = receipt;
  inspectionForm.value = {
    note: "",
    lines: receipt.lines
      .filter((l) => positive(l.pending_qty))
      .map((l) => ({
        receipt_line_id: l.id,
        name: l.name,
        lot: l.lot_no,
        pending: l.pending_qty,
        base_unit: l.base_unit,
        qualified_qty: quantity(l.pending_qty),
        qty_scale: receiptOrderLine(l.order_line_id)?.qty_scale ?? 6,
        rejected_qty: "0",
      })),
  };
  dialog.value = "inspect";
  inspectionConclusion.value = "";
  void loadReviewOptions();
}
async function saveInspection() {
  const receipt = inspectionReceipt.value;
  if (!receipt || reviewLoading.value || !inspectionConclusion.value) return;
  const issue = inspectionAmountIssue(inspectionForm.value.lines);
  if (issue) {
    showEntryIssue(inspectionEditor.value, issue, "qualified_qty");
    return;
  }
  entryError.value = "";
  await execute(
    `/api/v1/receipts/${receipt.id}/inspections`,
    {
      expected_version: receipt.version,
      note: inspectionForm.value.note,
      conclusion: inspectionConclusion.value,
      lines: inspectionForm.value.lines
        .filter((l) => positive(l.qualified_qty) || positive(l.rejected_qty))
        .map((l) => ({
          receipt_line_id: l.receipt_line_id,
          qualified_qty: l.qualified_qty,
          rejected_qty: l.rejected_qty,
        })),
    },
    done,
  );
}
async function post() {
  const p = posting.value;
  if (!p) return;
  await execute(
    `/api/v1/receipt-postings/${p.id}/post`,
    {
      expected_version: p.version,
      inspection_id: p.inspection_id,
      posting_segment_id: p.id,
    },
    done,
  );
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!dialog.value || !!returnSource.value, busy: busy.value }),
    "purchasing",
  ),
);
</script>
<template>
  <ReturnsPage
    v-if="returnSource && doc"
    :key="returnSource"
    :initial-source-id="returnSource"
    :initial-entity-id="doc.order.entity_id"
    embedded
    @failure="emit('failure', $event)"
    @back="
      returnSource = '';
      load();
    "
  />
  <div v-else ref="detailRoot" v-loading="loading">
    <div class="detail-toolbar">
      <el-button :disabled="busy" @click="emit('back')"
        >← 返回采购列表</el-button
      ><el-button :disabled="busy" @click="load">刷新单据</el-button>
    </div>
    <template v-if="doc">
      <AdmissionWarnings :lines="doc.lines" />
      <el-alert
        v-if="taskNote"
        :title="taskNote"
        type="info"
        :closable="false"
      />
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h3>采购单详情</h3>
            <span class="mono document-number">{{ doc.order.number }}</span>
          </div>
          <el-tag
            :type="doc.order.approval_state === 'APPROVED' ? 'success' : 'info'"
            >{{ labels[doc.order.approval_state] }} ·
            {{ labels[doc.order.fulfillment_state] }}</el-tag
          >
        </div>
        <dl class="facts">
          <dt>采购版本</dt>
          <dd>第 {{ doc.order.revision_no }} 版</dd>
          <dt>供应商</dt>
          <dd>{{ doc.order.supplier_name }}</dd>
          <dt>收货仓库</dt>
          <dd>{{ doc.order.warehouse_name }}</dd>
          <template v-if="doc.order.source_sales_order_id">
            <dt>补货来源</dt>
            <dd>
              {{ doc.order.source_sales_number || "关联销售单" }} ·
              创建时销售版本 {{ doc.order.source_sales_version }}
              <el-button
                v-if="can('sales.read')"
                link
                type="primary"
                @click="
                  emit('sale', {
                    id: doc.order.source_sales_order_id,
                    entity_id: doc.order.entity_id,
                  })
                "
                >查看来源销售单</el-button
              >
            </dd>
          </template>
        </dl>
        <el-alert
          v-if="doc.order.fulfillment_state === 'CLOSED'"
          title="未到货余量已关闭；已到货记录仍可继续验收及合格入库。"
          type="info"
          :closable="false"
        />
        <dl v-if="doc.order.closed_at" class="facts" aria-label="采购关闭记录">
          <dt>关闭时间</dt>
          <dd>{{ localTime(doc.order.closed_at) }}</dd>
          <dt>关闭人</dt>
          <dd :title="doc.order.closed_by || undefined">
            {{
              doc.order.closed_by === session?.principal_id
                ? session?.display_name
                : "其他成员"
            }}
          </dd>
          <dt>关闭原因</dt>
          <dd>{{ doc.order.close_reason }}</dd>
        </dl>
        <DocumentLineGrid
          :entity="doc.order.entity_id"
          stage="orders"
          :rows="doc.lines"
          title="采购订单明细"
        />
        <el-alert
          v-if="
            doc.order.approval_state === 'DRAFT' && doc.order.revision_no > 1
          "
          title="采购内容已修改，重新审核通过后才能登记到货。"
          type="warning"
          :closable="false"
        />
        <div
          v-if="
            can('purchase.write') &&
            ['DRAFT', 'APPROVED'].includes(doc.order.approval_state) &&
            doc.order.fulfillment_state === 'OPEN' &&
            !doc.receipts.length
          "
          class="form-actions"
        >
          <el-button :disabled="busy" @click="revise" :loading="revising"
            >修改采购单</el-button
          >
          <span class="form-note"
            >仅未登记到货时可修改，保存后须独立审核。</span
          >
        </div>
        <div class="form-actions" v-if="doc.order.approval_state === 'DRAFT'">
          <span v-if="selfReview" class="form-note"
            >制单人及本轮修改人员不能审核通过，请交由其他成员审核。</span
          >
          <el-button
            v-if="can('purchase.write')"
            :disabled="busy"
            @click="transition('cancel')"
            >取消采购单</el-button
          >
          <el-button
            v-if="can('purchase.approve')"
            type="primary"
            :disabled="busy || selfReview"
            @click="transition('approve')"
            >审核采购单</el-button
          >
        </div>
        <div
          class="form-actions"
          v-if="
            doc.order.approval_state === 'APPROVED' &&
            doc.order.fulfillment_state === 'OPEN'
          "
        >
          <el-button
            v-if="can('purchase.write')"
            :disabled="busy"
            @click="transition('close')"
            >关闭未收余量</el-button
          >
          <el-button
            v-if="can('purchase.receive')"
            type="primary"
            :disabled="busy"
            @click="receive"
            >登记到货</el-button
          >
        </div>
      </section>
      <PurchaseHistory
        :key="doc.order.version"
        :id="id"
        @failure="emit('failure', $event)"
      />
      <section class="receipt-overview">
        <h3>
          收货与验收 <small>{{ doc.receipts.length }} 次到货</small>
        </h3>
        <p>
          到货只登记保管数量；合格部分入库后，才增加可用库存、存货成本和采购应付。
        </p>
      </section>
      <el-empty v-if="!doc.receipts.length" description="尚未登记到货" />
      <section
        v-for="r in doc.receipts"
        :key="r.id"
        class="panel receipt-card"
        :data-purchase-receipt="r.id"
        tabindex="-1"
      >
        <div class="panel-heading">
          <div>
            <h3>到货记录 · {{ r.shipment_ref }}</h3>
            <span class="mono document-number">{{ r.number }}</span>
          </div>
          <el-button
            v-if="
              can('purchase.inspect') &&
              r.lines.some((l) => positive(l.pending_qty))
            "
            type="primary"
            :disabled="busy || r.created_by === session?.principal_id"
            @click="inspect(r)"
            >登记验收</el-button
          >
        </div>
        <p class="form-note">运输及随货记录：{{ r.transport_note }}</p>
        <p
          v-if="
            r.created_by === session?.principal_id &&
            r.lines.some((l) => positive(l.pending_qty))
          "
          class="form-note"
        >
          您是收货人，请由另一位成员验收。
        </p>
        <DocumentLineGrid
          :entity="doc.order.entity_id"
          stage="receiving"
          :rows="r.lines"
          title="到货明细"
          :data-testid="'receipt-lines-' + r.id"
        />
        <PurchaseRejectedHandover
          :receipt="r"
          @saved="done"
          @failure="emit('failure', $event)"
        />
        <details v-if="r.inspections.length" class="inspection-history">
          <summary>查看验收记录（含拒收结论）</summary>
          <div
            v-for="group in inspectionGroups(r)"
            :key="group.id"
            :data-purchase-inspection="group.id"
            tabindex="-1"
          >
            <p class="form-note">
              {{ localTime(group.rows[0]!.inspected_at) }} ·
              {{ group.rows[0]!.note }}
            </p>
            <DocumentLineGrid
              :entity="doc.order.entity_id"
              stage="inspection"
              :rows="group.rows"
              title="验收明细"
            />
          </div>
        </details>
        <div
          v-for="(p, index) in r.postings"
          :key="p.id"
          class="posting-row posting-document-row"
          :data-purchase-segment="p.id"
          tabindex="-1"
        >
          <div>
            <b
              >第 {{ index + 1 }} 次验收入库 ·
              {{
                p.state === "POSTED" ? "合格部分已入库" : "合格部分待入库"
              }}</b
            ><small>{{ p.note }}</small>
          </div>
          <div v-if="p.state === 'POSTED'" class="form-actions">
            <el-tag type="success">已入库</el-tag>
            <el-button
              v-if="p.posting_id && can('returns.read')"
              :disabled="busy"
              @click="returnSource = p.posting_id"
              >本次入库退货与进度</el-button
            >
          </div>
          <el-button
            v-else-if="can('purchase.post')"
            type="primary"
            plain
            :disabled="busy"
            @click="
              posting = p;
              dialog = 'post';
            "
            >合格入库</el-button
          >
          <DocumentLineGrid
            :entity="doc.order.entity_id"
            stage="posting"
            :rows="p.lines"
            :title="p.state === 'POSTED' ? '入库明细' : '待入库明细'"
          />
        </div>
      </section>
    </template>
  </div>
  <el-dialog
    :model-value="!!dialog"
    :title="
      (
        {
          approve: '审核采购单',
          cancel: '取消采购单',
          close: '关闭未收余量',
          receive: '登记到货',
          inspect: '登记验收',
          post: '确认合格入库',
        } as Record<string, string>
      )[dialog]
    "
    :width="
      ['receive', 'inspect', 'post'].includes(dialog)
        ? '96vw'
        : dialog === 'close'
          ? '860px'
          : '560px'
    "
    class="business-dialog"
    :class="{
      'purchase-handling-dialog': ['receive', 'inspect', 'post'].includes(
        dialog,
      ),
    }"
    :top="['receive', 'inspect', 'post'].includes(dialog) ? '4vh' : '15vh'"
    :close-on-click-modal="false"
    :show-close="!busy"
    :close-on-press-escape="!busy"
    @close="dialog = ''"
    destroy-on-close
  >
    <form
      v-if="['approve', 'cancel', 'close'].includes(dialog)"
      class="business-form"
      @keydown="formEnter"
      @submit.prevent="saveTransition"
    >
      <template v-if="dialog === 'close'">
        <p>
          确认本单以下未到货数量不再采购。关闭后不能继续登记到货；如需追加，请新建采购单。
        </p>
        <el-table :data="remaining" aria-label="本次关闭数量">
          <el-table-column prop="name" label="商品" min-width="180" />
          <el-table-column label="已收数量"
            ><template #default="{ row }"
              >{{ quantity(row.received_qty) }} {{ row.base_unit }}</template
            ></el-table-column
          >
          <el-table-column label="本次关闭数量"
            ><template #default="{ row }"
              >{{ quantity(row.remaining_qty) }} {{ row.base_unit }}</template
            ></el-table-column
          >
        </el-table>
        <p class="form-note">
          原订购数量与单价保留。已到货的待验、拒收保管、合格待入库以及原入库记录继续保留，按原流程处理；本次关闭不产生库存或应付过账。
        </p>
      </template>
      <template v-if="dialog === 'approve'">
        <label
          >审核意见<select
            v-model="approvalConclusion"
            aria-label="审核意见"
            required
            :disabled="reviewLoading || busy"
          >
            <option value="" disabled>
              {{ reviewLoading ? "正在读取字典…" : "请选择审核意见" }}
            </option>
            <option
              v-for="item in reviewItems('purchase_approval')"
              :key="item.value"
              :value="item.value"
            >
              {{ item.value }}
            </option>
          </select></label
        >
        <p class="form-note">
          确认审核后通过本单；字典只维护意见用语，不改变审核权限。补充说明可留空。
          <el-button
            link
            :disabled="busy || reviewLoading"
            @click="loadReviewOptions"
            >更新结论选项</el-button
          >
        </p>
        <p v-if="reviewError" role="alert">
          {{ reviewError }}
          <el-button link @click="loadReviewOptions">重新读取字典</el-button>
        </p>
      </template>
      <label
        >{{ dialog === "approve" ? "补充说明" : "操作说明"
        }}<input
          v-model="reason"
          :required="dialog !== 'approve'"
          :maxlength="
            dialog === 'approve' ? 200 - approvalConclusion.length - 1 : 200
          "
      /></label>
      <div class="form-actions">
        <el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="
            dialog === 'approve' &&
            (reviewLoading || !!reviewError || !approvalConclusion)
          "
          >{{
            dialog === "approve"
              ? "确认审核"
              : dialog === "close"
                ? "确认关闭余量"
                : "确认取消"
          }}</el-button
        >
      </div>
    </form>
    <form
      v-if="dialog === 'receive'"
      class="business-form"
      ref="receiptEditor"
      @keydown="formEnter"
      @submit.prevent="saveReceipt"
    >
      <p class="form-note">
        已按订单顺序带出全部待收行，数量默认为待收余量（基本单位）。本次未到货可填
        0 或移除此行；同一商品分批号时请调整各行数量合计。批号和日期须据实填写。
      </p>
      <div class="form-grid">
        <label
          >随货单号<input
            v-model="receiptForm.shipment_ref"
            required
            maxlength="120" /></label
        ><label
          >运输及随货核查<input
            v-model="receiptForm.transport_note"
            required
            maxlength="500"
        /></label>
      </div>
      <DocumentLineGrid
        v-if="doc"
        :entity="doc.order.entity_id"
        stage="receiving"
        mode="editor"
        :rows="receiptRows"
        title="本次到货明细"
        :disabled="busy"
        actions
      >
        <template #cell="{ row, index, column }">
          <select
            v-if="column.key === 'product'"
            v-model="receiptForm.lines[index]!.order_line_id"
            aria-label="采购商品"
            @change="changeReceiptProduct(index)"
            required
            :tabindex="column.tab_stop ? 0 : -1"
          >
            <option v-for="item in remaining" :key="item.id" :value="item.id">
              {{ item.name }} · 待收 {{ quantity(item.remaining_qty) }}
              {{ item.base_unit }}
            </option>
          </select>
          <select
            v-else-if="column.key === 'location'"
            v-model="receiptForm.lines[index]!.location_id"
            aria-label="货位"
            :disabled="busy || !positive(receiptForm.lines[index]!.qty)"
            :required="positive(receiptForm.lines[index]!.qty)"
            :tabindex="column.tab_stop ? 0 : -1"
          >
            <option v-for="loc in locations" :key="loc.id" :value="loc.id">
              {{ loc.code }}{{ loc.name !== loc.code ? " · " + loc.name : "" }}
            </option>
          </select>
          <input
            v-else-if="column.key === 'qty'"
            v-model="receiptForm.lines[index]!.qty"
            aria-label="到货数量"
            required
            inputmode="decimal"
            pattern="[0-9]+([.][0-9]{1,6})?"
            maxlength="21"
            :tabindex="column.tab_stop ? 0 : -1"
          />
          <input
            v-else-if="column.key === 'lot_no'"
            v-model="receiptForm.lines[index]!.lot_no"
            aria-label="生产批号"
            :disabled="busy || !positive(receiptForm.lines[index]!.qty)"
            :required="positive(receiptForm.lines[index]!.qty)"
            maxlength="100"
            :tabindex="column.tab_stop ? 0 : -1"
          />
          <DateInput
            v-else-if="column.key === 'mfg_date'"
            v-model="receiptForm.lines[index]!.mfg_date"
            aria-label="生产日期"
            advance-on-enter
            :max="businessToday()"
            :disabled="busy || !positive(receiptForm.lines[index]!.qty)"
            :required="positive(receiptForm.lines[index]!.qty)"
            :tabindex="column.tab_stop ? 0 : -1"
          />
          <DateInput
            v-else-if="column.key === 'expiry_date'"
            v-model="receiptForm.lines[index]!.expiry_date"
            aria-label="有效期至"
            advance-on-enter
            :min="receiptForm.lines[index]!.mfg_date"
            max="9999-12-30"
            :disabled="busy || !positive(receiptForm.lines[index]!.qty)"
            :required="positive(receiptForm.lines[index]!.qty)"
            :tabindex="column.tab_stop ? 0 : -1"
          />
          <span v-else :title="documentCell(row, column)">{{
            documentCell(row, column)
          }}</span>
        </template>
        <template #actions="{ index }"
          ><el-button
            link
            :disabled="busy || receiptForm.lines.length === 1"
            @click="receiptForm.lines.splice(index, 1)"
            >移除此行</el-button
          ></template
        >
      </DocumentLineGrid>
      <details class="receipt-unit-details">
        <summary>
          查看采购单位与基本单位换算（{{ receiptForm.lines.length }} 行）
        </summary>
        <p
          v-for="(line, index) in receiptForm.lines"
          :key="line.client_key"
          class="form-note receipt-unit-note"
        >
          第 {{ index + 1 }} 行：{{ receiptUnitNote(line.order_line_id) }}
        </p>
      </details>
      <p v-if="entryError" role="alert" class="purchase-entry-error">
        {{ entryError }}
      </p>
      <div class="form-actions">
        <el-button
          class="receipt-add-line"
          :disabled="busy || receiptForm.lines.length >= 100"
          @click="addReceiptLine"
          >添加到货行</el-button
        >
        <el-button native-type="submit" type="primary" :loading="busy"
          >保存到货记录</el-button
        >
      </div>
    </form>
    <form
      v-if="dialog === 'inspect'"
      class="business-form"
      ref="inspectionEditor"
      @keydown="formEnter"
      @submit.prevent="saveInspection"
    >
      <p class="form-note">
        合格数量默认等于本次待验余量，拒收数量默认为
        0；请按实际核验结果修改。合格＋拒收不得超过待验余量，两项都填 0
        表示本次暂不验收。默认回车直达下一行合格数量，拒收列可点击或在列配置中设为停留。
      </p>
      <DocumentLineGrid
        v-if="doc"
        :entity="doc.order.entity_id"
        stage="inspection"
        mode="editor"
        :rows="inspectionRows"
        title="本次验收明细"
        :disabled="busy"
      >
        <template #cell="{ row, index, column }">
          <input
            v-if="column.key === 'qualified_qty'"
            v-model="inspectionForm.lines[index]!.qualified_qty"
            aria-label="本次合格数量"
            required
            inputmode="decimal"
            pattern="[0-9]+([.][0-9]{1,6})?"
            maxlength="21"
            :tabindex="column.tab_stop ? 0 : -1"
          />
          <input
            v-else-if="column.key === 'rejected_qty'"
            v-model="inspectionForm.lines[index]!.rejected_qty"
            aria-label="本次拒收数量"
            required
            inputmode="decimal"
            pattern="[0-9]+([.][0-9]{1,6})?"
            maxlength="21"
            :tabindex="column.tab_stop ? 0 : -1"
          />
          <span v-else :title="documentCell(row, column)">{{
            documentCell(row, column)
          }}</span>
        </template>
      </DocumentLineGrid>
      <div class="form-grid purchase-review-fields">
        <label
          >验收结论<select
            v-model="inspectionConclusion"
            aria-label="验收结论"
            required
            :disabled="reviewLoading || busy"
          >
            <option value="" disabled>
              {{ reviewLoading ? "正在读取字典…" : "请选择验收结论" }}
            </option>
            <option
              v-for="item in reviewItems(inspectionKey)"
              :key="item.value"
              :value="item.value"
            >
              {{ item.value }}
            </option>
          </select></label
        >
        <label
          >验收依据与结论补充<textarea
            v-model="inspectionForm.note"
            aria-label="验收依据与结论"
            :required="inspectionKey !== 'inspection_qualified'"
            :maxlength="500 - inspectionConclusion.length - 1"
            rows="2"
            :placeholder="
              inspectionKey === 'inspection_qualified'
                ? '可补充验收凭据和核验项目；无异常可留空'
                : '存在拒收数量，请填写核验依据及拒收原因'
            "
          />
        </label>
      </div>
      <p class="form-note">
        结论选项随本次合格、拒收数量联动；各类常用用语可在“资料 →
        数据字典”维护，排序靠前的启用值为默认值。
        <el-button
          link
          :disabled="busy || reviewLoading"
          @click="loadReviewOptions"
          >更新结论选项</el-button
        >
      </p>
      <p v-if="reviewError" role="alert">
        {{ reviewError }}
        <el-button link @click="loadReviewOptions">重新读取字典</el-button>
      </p>
      <p v-if="entryError" role="alert" class="purchase-entry-error">
        {{ entryError }}
      </p>
      <div class="form-actions">
        <el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="
            reviewLoading ||
            !!reviewError ||
            !inspectionConclusion ||
            !inspectionForm.lines.some(
              (l) => positive(l.qualified_qty) || positive(l.rejected_qty),
            )
          "
          >保存验收记录</el-button
        >
      </div>
    </form>
    <form v-if="dialog === 'post'" class="business-form" @submit.prevent="post">
      <p>将下列合格货物正式入库，同时登记存货成本与供应商应付。</p>
      <DocumentLineGrid
        v-if="doc && posting"
        :entity="doc.order.entity_id"
        stage="posting"
        mode="editor"
        :rows="posting.lines"
        title="确认入库明细"
        :disabled="busy"
      />
      <p class="form-note">验收结论：{{ posting?.note }}</p>
      <p class="form-note">
        入库记录保存后不可直接修改；调整需通过后续冲销或退货流程处理。
      </p>
      <div class="form-actions">
        <el-button :disabled="busy" @click="dialog = ''">返回核对</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >确认入库</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>

<style>
.posting-document-row {
  flex-wrap: wrap;
}
.posting-document-row > .document-line-grid {
  flex-basis: 100%;
  width: 100%;
}
.purchase-handling-dialog {
  max-height: 92dvh;
  display: flex;
  flex-direction: column;
}
.purchase-handling-dialog .el-dialog__body {
  min-height: 0;
  max-height: none;
  overflow: auto;
}
.purchase-handling-dialog .el-dialog__header {
  flex: none;
}
.purchase-handling-dialog .document-grid-scroll {
  max-height: clamp(160px, calc(100dvh - 470px), 440px);
}
.purchase-handling-dialog form > .form-actions {
  position: sticky;
  bottom: -4px;
  z-index: 2;
  margin: 8px 0 0;
  padding: 10px 0;
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color-lighter);
}
.purchase-handling-dialog .receipt-add-line {
  margin-right: auto;
}
</style>

<style scoped>
.receipt-unit-details {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 8px 0 12px;
}
.receipt-unit-details summary {
  cursor: pointer;
}
.receipt-unit-details[open] {
  max-height: 160px;
  overflow: auto;
}
.purchase-entry-error {
  color: var(--el-color-danger);
  font-size: 13px;
}
.purchase-review-fields {
  grid-template-columns: minmax(180px, 1fr) minmax(240px, 3fr);
}
@media (max-width: 650px) {
  .purchase-review-fields {
    grid-template-columns: 1fr;
  }
}
</style>
