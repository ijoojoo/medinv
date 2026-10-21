import { computed, reactive, ref, watch } from "vue";
import { guardCatalogLeave, hasCatalogChanges } from "./catalogChanges";
import { askCloseChanges } from "./closeChanges";
import { ElMessageBox } from "element-plus";
import { ApiError, request, session } from "./api";
import {
  newEntryLine,
  type EntryChannel,
  type EntryLine,
} from "./documentEntry";
import { itemForPage } from "./navigation";
import { unresolved } from "./commandRecovery";
import { activeEntity, selectEntity } from "./entityScope";
import type { WorkbenchVisit } from "./workQueue";

export interface EditorContent {
  supplier_id: string;
  customer_id: string;
  warehouse_id: string;
  delivery_address: string;
  minimum_shelf_days: string;
  credit_days: string;
  reason: string;
  lines: EntryLine[];
}
export interface DraftRecord {
  id: string;
  channel: EntryChannel;
  entity_id: string;
  title: string;
  source_order_id: string | null;
  source_version: string | null;
  version: string;
  state: "EDITING" | "CONVERTED" | "DISCARDED";
  content: EditorContent;
  converted_order_id: string | null;
  updated_at: string;
}
interface SaveAttempt {
  body: string;
  key: string;
}
export interface EditorState {
  record: DraftRecord;
  content: EditorContent;
  saved: string;
  savedTitle: string;
  error: string;
  saving: boolean;
  submitting: boolean;
  conflict: boolean;
  pending: SaveAttempt | null;
  continuation?: { order_id: string; carry_header: boolean };
}
export const workspacePage = ref("overview");
export interface ReferenceTarget {
  page: string;
  id: string;
  tab: string;
  entity: string;
  token: number;
}
export const referenceNavigation = ref<ReferenceTarget | null>(null);
export function clearReference(page: string) {
  if (referenceNavigation.value?.page !== page) return;
  referenceNavigation.value = null;
  rememberRoute(true);
}

export async function showReference(page: string, id: string, tab = "") {
  if (!["products", "parties", "quality-ledgers"].includes(page)) return;
  if (!(await guardCatalogLeave(page))) return;
  goPage(page);
  if (workspacePage.value !== page) return;
  referenceNavigation.value = {
    page,
    id,
    tab,
    entity: activeEntity.value,
    token: Date.now(),
  };
  rememberRoute();
}
export const activeEditor = ref("");
export const activeRecord = ref("");
export const recordTabs = ref<
  {
    key: string;
    channel: EntryChannel;
    id: string;
    entity: string;
    title: string;
    origin?: WorkbenchVisit;
  }[]
>([]);
export async function closeRecordTab(key: string) {
  if (!(await guardCatalogLeave(key))) return;
  const tab = recordTabs.value.find((t) => t.key === key);
  recordTabs.value = recordTabs.value.filter((t) => t.key !== key);
  if (activeRecord.value === key) {
    if (tab?.origin) returnToWorkbench(tab.origin);
    else goPage(moduleFor(tab?.channel || "purchase"));
  }
}
export const workbenchReturn = ref<{
  visit: WorkbenchVisit;
  token: number;
} | null>(null);
let returnSequence = 0;
export function returnToWorkbench(visit: WorkbenchVisit) {
  if (visit.item.entity_id !== activeEntity.value) return;
  workbenchReturn.value = { visit, token: ++returnSequence };
  goPage("overview");
}
watch(
  activeEditor,
  (value) => {
    if (value) activeRecord.value = "";
  },
  { flush: "sync" },
);
export const openedEditors = ref<string[]>([]);
export const moduleTabs = ref<string[]>(["overview"]);
export const editors = reactive<Record<string, EditorState>>({});
export const editorLoading = ref(false),
  editorError = ref("");
export const recordNavigation = ref<{
  channel: EntryChannel;
  id: string;
  entity: string;
  token: number;
} | null>(null);
export const recordSaved = ref<{
  channel: EntryChannel;
  id: string;
  entity: string;
  token: number;
} | null>(null);
let routeReady = false,
  navigating = false,
  generation = 0;
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const inFlight = new Map<string, Promise<void>>();
const scope = () =>
  `${session.value?.tenant_id || ""}:${session.value?.principal_id || ""}`;
const backupKey = (id: string) => `medinv.editor.v1:${scope()}:${id}`;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const moduleFor = (channel: EntryChannel) =>
  channel === "purchase" ? "purchasing" : "selling";
export const blankContent = (): EditorContent => ({
  supplier_id: "",
  customer_id: "",
  warehouse_id: "",
  delivery_address: "",
  minimum_shelf_days: "0",
  credit_days: "30",
  reason: "",
  lines: [newEntryLine()],
});
export function contentWire(content: EditorContent): EditorContent {
  return {
    supplier_id: content.supplier_id,
    customer_id: content.customer_id,
    warehouse_id: content.warehouse_id,
    delivery_address: content.delivery_address,
    minimum_shelf_days: String(content.minimum_shelf_days),
    credit_days: String(content.credit_days),
    reason: content.reason,
    lines: content.lines.map((r) => ({
      client_key: r.client_key,
      sku_id: r.sku_id,
      packaging_id: r.packaging_id,
      qty: r.qty,
      unit_price: r.unit_price,
      tax_rate: r.tax_rate,
      search_text: r.search_text || "",
    })),
  };
}
const signature = (content: EditorContent) =>
  JSON.stringify(contentWire(content));
export const isDirty = (state: EditorState) =>
  signature(state.content) !== state.saved ||
  state.record.title !== state.savedTitle ||
  !!state.pending;
export const workspaceUnsaved = computed(() =>
  Object.values(editors).some((e) => isDirty(e) || e.saving || e.submitting),
);
function rememberRoute(replace = false) {
  if (!routeReady || navigating || !session.value) return;
  let path = `/workspace/${workspacePage.value}`;
  const state = editors[activeEditor.value];
  if (activeEditor.value) path += `/drafts/${activeEditor.value}`;
  const params = new URLSearchParams({ tenant: session.value.tenant_id });
  const target = recordNavigation.value;
  const reference = referenceNavigation.value;
  if (
    !activeEditor.value &&
    reference?.page === workspacePage.value &&
    reference.entity === activeEntity.value
  ) {
    params.set("record", reference.id);
    params.set("tab", reference.tab);
    params.set("entity", reference.entity);
  }
  if (
    !activeEditor.value &&
    target &&
    moduleFor(target.channel) === workspacePage.value
  ) {
    params.set("order", target.id);
    params.set("entity", target.entity);
  }
  const url = `${path}?${params}`;
  if (location.pathname + location.search !== url)
    history[replace ? "replaceState" : "pushState"]({}, "", url);
  if (state && !openedEditors.value.includes(state.record.id))
    openedEditors.value.push(state.record.id);
}
watch(
  workspacePage,
  (value) => {
    if (!moduleTabs.value.includes(value)) moduleTabs.value.push(value);
    if (!navigating) {
      activeEditor.value = "";
      activeRecord.value = "";
      recordNavigation.value = null;
      rememberRoute();
    }
  },
  { flush: "sync" },
);
export function goPage(page: string) {
  if (
    page !== "overview" &&
    page !== "appearance" &&
    !session.value?.permissions.includes(itemForPage(page)?.permission || "")
  )
    return;
  activeRecord.value = "";
  referenceNavigation.value = null;
  if (!moduleTabs.value.includes(page)) moduleTabs.value.push(page);
  navigating = true;
  workspacePage.value = page;
  activeEditor.value = "";
  recordNavigation.value = null;
  navigating = false;
  rememberRoute();
}
export async function closeModuleTab(page: string) {
  if (page === "overview" || !moduleTabs.value.includes(page)) return;
  if (!(await guardCatalogLeave(page))) return;
  const index = moduleTabs.value.indexOf(page);
  const remaining = moduleTabs.value.filter((p) => p !== page);
  if (!activeEditor.value && workspacePage.value === page)
    goPage(remaining[Math.max(0, index - 1)] || "overview");
  moduleTabs.value = remaining;
}
export function showRecord(
  channel: EntryChannel,
  id: string,
  entity: string,
  title?: string,
  origin?: WorkbenchVisit,
) {
  if (entity !== activeEntity.value) {
    editorError.value = "该单据属于其他经营主体，请先在右上角切换主体。";
    return;
  }
  navigating = true;
  workspacePage.value = moduleFor(channel);
  activeEditor.value = "";
  const key = `${scope()}:${entity}:${channel}:${id}:view`;
  if (!recordTabs.value.some((t) => t.key === key))
    recordTabs.value.push({
      key,
      channel,
      id,
      entity,
      title:
        title ||
        `${channel === "purchase" ? "采购单" : "销售单"} · ${id.slice(0, 8)}`,
    });
  const tab = recordTabs.value.find((t) => t.key === key)!;
  if (origin) tab.origin = origin;
  activeRecord.value = key;
  recordNavigation.value = { channel, id, entity, token: Date.now() };
  navigating = false;
  rememberRoute();
}
function backup(state: EditorState) {
  try {
    sessionStorage.setItem(
      backupKey(state.record.id),
      JSON.stringify({
        record: { ...state.record, content: contentWire(state.content) },
        base: state.record.version,
        content: contentWire(state.content),
        pending: state.pending,
        time: Date.now(),
      }),
    );
  } catch {
    state.error =
      "本机恢复记录无法写入，请尽快保存草稿；刷新前请核对保存状态。";
  }
}
function clearBackup(id: string) {
  try {
    sessionStorage.removeItem(backupKey(id));
  } catch {
    /* server copy remains authoritative */
  }
}
export function editorChanged(id: string) {
  const state = editors[id];
  if (!state || state.record.state !== "EDITING") return;
  backup(state);
  clearTimeout(timers.get(id));
  if (!state.conflict && !state.submitting && isDirty(state))
    timers.set(
      id,
      setTimeout(() => {
        void saveDraft(id).catch(() => {});
      }, 1400),
    );
}
async function sendDraft(id: string) {
  const state = editors[id];
  if (!state || state.record.state !== "EDITING") return;
  if (state.conflict)
    throw new Error(state.error || "草稿版本冲突，请先核对已保存版本");
  const currentScope = scope(),
    gen = generation;
  state.saving = true;
  state.error = "";
  try {
    // Retry the immutable save before sending later edits; do not invent a new key on a lost response.
    while (isDirty(state)) {
      if (!state.pending)
        state.pending = {
          key: crypto.randomUUID(),
          body: JSON.stringify({
            expected_version: state.record.version,
            channel: state.record.channel,
            entity_id: state.record.entity_id,
            source_order_id: state.record.source_order_id,
            source_version: state.record.source_version,
            title: state.record.title,
            content: contentWire(state.content),
          }),
        };
      backup(state);
      const attempt = state.pending;
      const controller = new AbortController(),
        timeout = setTimeout(() => controller.abort(), 15000);
      let result: DraftRecord;
      try {
        result = await request<DraftRecord>(`/api/v1/document-drafts/${id}`, {
          method: "POST",
          body: attempt.body,
          headers: { "Idempotency-Key": attempt.key },
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeout);
      }
      if (
        gen !== generation ||
        scope() !== currentScope ||
        editors[id] !== state
      )
        return;
      const latestTitle = state.record.title;
      state.record = { ...result, title: latestTitle };
      state.saved = signature(result.content);
      state.savedTitle = result.title;
      state.pending = null;
      if (!isDirty(state)) clearBackup(id);
      else backup(state);
    }
  } catch (e) {
    if (gen !== generation || scope() !== currentScope) throw e;
    if (
      e instanceof ApiError &&
      e.status >= 400 &&
      e.status < 500 &&
      e.status !== 408
    ) {
      state.pending = null;
      if (
        [
          "DRAFT_VERSION_CONFLICT",
          "DRAFT_CLOSED",
          "DRAFT_SOURCE_MISMATCH",
        ].includes(e.code)
      )
        state.conflict = true;
    }
    state.error = e instanceof Error ? e.message : "草稿保存失败，输入仍保留";
    backup(state);
    throw e;
  } finally {
    state.saving = false;
  }
}
export async function saveDraft(id: string) {
  clearTimeout(timers.get(id));
  while (inFlight.has(id)) await inFlight.get(id);
  if (!editors[id] || !isDirty(editors[id])) return;
  const task = sendDraft(id);
  inFlight.set(id, task);
  try {
    await task;
  } finally {
    if (inFlight.get(id) === task) inFlight.delete(id);
  }
}
function installDraft(record: DraftRecord) {
  const state: EditorState = {
    record,
    content: structuredClone(record.content),
    saved: signature(record.content),
    savedTitle: record.title,
    error: "",
    saving: false,
    submitting: false,
    conflict: false,
    pending: null,
  };
  if (!state.content.lines.length) state.content.lines = [newEntryLine()];
  try {
    const raw = sessionStorage.getItem(backupKey(record.id));
    if (raw && raw.length < 200000 && record.state === "EDITING") {
      const local = JSON.parse(raw);
      if (
        Date.now() - local.time < 86400000 &&
        local.content &&
        Array.isArray(local.content.lines) &&
        local.content.lines.length <= 50
      ) {
        const candidate = contentWire(local.content);
        if (candidate.lines.every((l) => uuid.test(l.client_key))) {
          state.content = candidate;
          if (typeof local.record?.title === "string")
            state.record = { ...record, title: local.record.title };
          if (local.base === record.version)
            state.pending = local.pending || null;
          else if (
            local.pending &&
            signature(JSON.parse(local.pending.body).content) === state.saved
          )
            state.pending = null;
          else if (signature(candidate) !== state.saved) {
            state.conflict = true;
            state.error =
              "本机恢复内容与服务端版本不同，已保留本机内容。请核对后选择读取已存版本或另存草稿。";
          }
        }
      }
    }
  } catch {
    state.error = "本机恢复内容不可读取，已加载服务器草稿。";
  }
  editors[record.id] = state;
  if (!openedEditors.value.includes(record.id))
    openedEditors.value.push(record.id);
  return editors[record.id]!;
}
export async function openDraft(
  id: string,
  channel?: EntryChannel,
  replace = false,
) {
  const gen = generation;
  editorLoading.value = true;
  editorError.value = "";
  navigating = true;
  if (channel) workspacePage.value = moduleFor(channel);
  activeEditor.value = id;
  navigating = false;
  rememberRoute(replace);
  try {
    let state = editors[id];
    if (!state) {
      let record: DraftRecord;
      try {
        record = await request<DraftRecord>(`/api/v1/document-drafts/${id}`);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404) || gen !== generation)
          throw e;
        const local = JSON.parse(
          sessionStorage.getItem(backupKey(id)) || "null",
        );
        if (
          !local ||
          Date.now() - local.time > 86400000 ||
          local.record?.version !== "0" ||
          local.record.id !== id ||
          !local.pending ||
          !session.value?.entities.some((x) => x.id === local.record.entity_id)
        )
          throw e;
        record = local.record;
      }
      if (gen !== generation) return;
      if (channel && record.channel !== channel)
        throw new Error("草稿类型与地址不符");
      state = installDraft(record);
      if (record.version === "0") state.saved = "";
    }
    if (gen !== generation) return;
    if (channel && state.record.channel !== channel)
      throw new Error("草稿类型与地址不符");
    if (state.record.entity_id !== activeEntity.value) {
      if (!(await guardCatalogLeave())) {
        goPage("overview");
        return;
      }
      if (!selectEntity(state.record.entity_id))
        throw new Error("无权访问此草稿所属经营主体");
    }
    if (activeEditor.value === id) {
      navigating = true;
      workspacePage.value = moduleFor(state.record.channel);
      navigating = false;
      rememberRoute(true);
    }
  } catch (e) {
    if (gen === generation)
      editorError.value = e instanceof Error ? e.message : "无法读取草稿";
  } finally {
    if (gen === generation) editorLoading.value = false;
  }
}
export async function createEditor(
  channel: EntryChannel,
  entity: string,
  content?: Partial<EditorContent>,
  source?: { id: string; version: string },
  title = "",
  continuation?: EditorState["continuation"],
) {
  const id = crypto.randomUUID();
  const record: DraftRecord = {
    id,
    channel,
    entity_id: entity,
    title,
    source_order_id: source?.id || null,
    source_version: source?.version || null,
    version: "0",
    state: "EDITING",
    content: { ...blankContent(), ...content },
    converted_order_id: null,
    updated_at: "",
  };
  const state = installDraft(record);
  state.continuation = continuation;
  state.saved = "";
  navigating = true;
  workspacePage.value = moduleFor(channel);
  activeEditor.value = id;
  navigating = false;
  rememberRoute();
  await saveDraft(id); // Opening itself is recoverable even before the first line is complete.
  return id;
}
export async function reloadDraft(id: string) {
  await ElMessageBox.confirm(
    "将读取服务器已保存版本，并放弃当前窗口尚未保存的修改。",
    "读取已存版本",
    {
      confirmButtonText: "读取已存版本",
      cancelButtonText: "继续编辑",
      type: "warning",
    },
  );
  const gen = generation;
  const record = await request<DraftRecord>(`/api/v1/document-drafts/${id}`);
  if (gen !== generation) return;
  clearBackup(id);
  delete editors[id];
  installDraft(record);
}
export async function copyDraft(id: string) {
  const state = editors[id];
  if (!state) return;
  return createEditor(
    state.record.channel,
    state.record.entity_id,
    JSON.parse(signature(state.content)),
    state.record.source_order_id
      ? {
          id: state.record.source_order_id,
          version: state.record.source_version!,
        }
      : undefined,
    state.record.title,
  );
}
function removeEditor(id: string) {
  clearTimeout(timers.get(id));
  delete editors[id];
  openedEditors.value = openedEditors.value.filter((x) => x !== id);
  clearBackup(id);
}
export async function closeEditor(id: string) {
  const state = editors[id];
  if (state?.submitting || state?.saving)
    throw new Error("正在保存，请完成后关闭");
  if (state && isDirty(state)) {
    const choice = await askCloseChanges(
      "当前单据有尚未保存的修改。保存将沿用已有的单据草稿，不会自动提交审核。",
    );
    if (choice === "cancel") return;
    if (choice === "save") {
      await saveDraft(id);
      if (isDirty(state)) return;
    }
  }
  const channel =
    state?.record.channel ||
    (workspacePage.value === "selling" ? "sale" : "purchase");
  removeEditor(id);
  if (activeEditor.value === id) goPage(moduleFor(channel));
}
export async function guardWorkspaceLeave() {
  if (!(await guardCatalogLeave())) return false;
  if (Object.values(editors).some((s) => s.submitting) || unresolved.value) {
    await ElMessageBox.alert(
      "请先确认当前提交结果，再切换企业或退出。",
      "提交尚未结束",
      { confirmButtonText: "知道了" },
    );
    return false;
  }
  if (!workspaceUnsaved.value) return true;
  try {
    await ElMessageBox.confirm(
      "离开前将保存各张单据的编辑草稿，未完成的单据不会进入审核。",
      "保存草稿后离开",
      {
        confirmButtonText: "保存并继续",
        cancelButtonText: "留在当前企业",
        closeOnClickModal: false,
      },
    );
    for (const id of openedEditors.value) await saveDraft(id);
    return true;
  } catch {
    return false;
  }
}
export function convertedEditor(id: string, order: string) {
  const state = editors[id];
  if (!state) return;
  const { channel, entity_id } = state.record;
  recordSaved.value = {
    channel,
    entity: entity_id,
    id: order,
    token: Date.now(),
  };
  removeEditor(id);
  showRecord(channel, order, entity_id);
}
export async function createNextEditor(
  id: string,
  order: string,
  carry: boolean,
) {
  const state = editors[id];
  if (!state || state.record.source_order_id) return;
  const { channel, entity_id } = state.record;
  const c = state.content;
  // Explicit header allowlist: never carry lines, titles, revisions or source IDs.
  const header: Partial<EditorContent> | undefined = carry
    ? {
        warehouse_id: c.warehouse_id,
        ...(channel === "purchase"
          ? { supplier_id: c.supplier_id }
          : {
              customer_id: c.customer_id,
              delivery_address: c.delivery_address,
              minimum_shelf_days: String(c.minimum_shelf_days),
              credit_days: String(c.credit_days),
            }),
      }
    : undefined;
  // Retire the committed source before opening another recoverable draft.
  convertedEditor(id, order);
  try {
    return await createEditor(channel, entity_id, header, undefined, "", {
      order_id: order,
      carry_header: carry,
    });
  } catch {
    throw new Error(
      "上一单已保存；下一张草稿暂未保存成功，请在当前新单重试保存草稿。",
    );
  }
}
export async function refreshConvertedDrafts() {
  let converted = false;
  const gen = generation;
  for (const id of [...openedEditors.value]) {
    if (editors[id]?.record.version === "0") continue;
    const record = await request<DraftRecord>(`/api/v1/document-drafts/${id}`);
    if (gen !== generation) return false;
    if (record.state === "CONVERTED" && record.converted_order_id) {
      convertedEditor(id, record.converted_order_id);
      converted = true;
    }
  }
  return converted;
}
async function readRoute() {
  if (!session.value) return;
  const url = new URL(location.href),
    parts = url.pathname.split("/").filter(Boolean);
  if (
    url.searchParams.get("tenant") &&
    url.searchParams.get("tenant") !== session.value.tenant_id
  ) {
    editorError.value = "该地址属于另一家企业，请先切换到对应企业后重新打开。";
    goPage("overview");
    return;
  }
  const page = parts[0] === "workspace" ? parts[1] || "overview" : "overview";
  if (
    page !== "overview" &&
    page !== "appearance" &&
    !session.value.permissions.includes(itemForPage(page)?.permission || "")
  ) {
    goPage("overview");
    return;
  }
  navigating = true;
  workspacePage.value = page;
  activeEditor.value = "";
  activeRecord.value = "";
  recordNavigation.value = null;
  referenceNavigation.value = null;
  navigating = false;
  if (
    parts[2] === "drafts" &&
    uuid.test(parts[3] || "") &&
    ["purchasing", "selling"].includes(page)
  )
    await openDraft(
      parts[3]!,
      page === "purchasing" ? "purchase" : "sale",
      true,
    );
  else if (
    ["purchasing", "selling"].includes(page) &&
    uuid.test(url.searchParams.get("order") || "") &&
    uuid.test(url.searchParams.get("entity") || "")
  ) {
    const entity = url.searchParams.get("entity")!;
    if (entity !== activeEntity.value) {
      if (!(await guardCatalogLeave()) || !selectEntity(entity)) {
        goPage("overview");
        return;
      }
    }
    showRecord(
      page === "purchasing" ? "purchase" : "sale",
      url.searchParams.get("order")!,
      entity,
    );
  } else if (
    ["products", "parties", "quality-ledgers"].includes(page) &&
    uuid.test(url.searchParams.get("record") || "") &&
    uuid.test(url.searchParams.get("entity") || "")
  ) {
    const entity = url.searchParams.get("entity")!;
    if (
      entity !== activeEntity.value &&
      (!(await guardCatalogLeave()) || !selectEntity(entity))
    ) {
      goPage("overview");
      return;
    }
    await showReference(
      page,
      url.searchParams.get("record")!,
      url.searchParams.get("tab") || "",
    );
  }
  rememberRoute(true);
}
export function initializeWorkspace(resetRoute = false) {
  routeReady = true;
  if (resetRoute) {
    goPage("overview");
  } else void readRoute();
}
export function resetWorkspace() {
  generation++;
  routeReady = false;
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  for (const id of Object.keys(editors)) delete editors[id];
  openedEditors.value = [];
  recordTabs.value = [];
  activeRecord.value = "";
  moduleTabs.value = ["overview"];
  activeEditor.value = "";
  recordNavigation.value = null;
  recordSaved.value = null;
  referenceNavigation.value = null;
  workbenchReturn.value = null;
  editorLoading.value = false;
  editorError.value = "";
  workspacePage.value = "overview";
}
window.addEventListener("popstate", () => {
  if (routeReady) void readRoute();
});
window.addEventListener("beforeunload", (e) => {
  if (workspaceUnsaved.value || hasCatalogChanges()) {
    e.preventDefault();
    e.returnValue = "";
  }
});
export function listCacheKey(channel: EntryChannel) {
  return `medinv.list.v2:${scope()}:${activeEntity.value}:${channel}`;
}
