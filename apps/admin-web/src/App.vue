<script setup lang="ts">
import "./master.css";
import { activeEntity, selectEntity } from "./entityScope";
import {
  computed,
  nextTick,
  defineAsyncComponent,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from "vue";
import { ElMessage } from "element-plus";
import BusinessNavigation from "./BusinessNavigation.vue";
import CloseChangesDialog from "./CloseChangesDialog.vue";
import { hasCatalogChanges, revealChanges } from "./catalogChanges";
import DocumentEditor from "./DocumentEditor.vue";
import DocumentRecord from "./DocumentRecord.vue";
import { appearance } from "./appearance";
import {
  workspacePage as page,
  activeEditor,
  activeRecord,
  recordTabs,
  referenceNavigation,
  closeRecordTab,
  showRecord,
  openedEditors,
  editors,
  moduleTabs,
  editorError,
  editorLoading,
  goPage,
  openDraft,
  closeEditor,
  closeModuleTab,
  isDirty,
  resetWorkspace,
  initializeWorkspace,
  guardWorkspaceLeave,
  refreshConvertedDrafts,
  workbenchReturn,
  returnToWorkbench,
} from "./workspace";
const openEditorIds = computed(() =>
  openedEditors.value.filter((key) => !!editors[key]),
);
import WorkDashboard from "./WorkDashboard.vue";
import AppearanceSettings from "./AppearanceSettings.vue";
import CommandRecovery from "./CommandRecovery.vue";
import type {
  WorkQueueFilters,
  WorkQueueItem,
  WorkbenchVisit,
} from "./workQueue";
import { selectHomeView } from "./workbench";
const tabBar = ref<HTMLElement>(),
  workArea = ref<HTMLElement>();
watch(workArea, (area, _old, cleanup) => {
  if (!area) return;
  const observer = new ResizeObserver(() =>
    document.documentElement.style.setProperty(
      "--ui-workspace-height",
      `${area.clientHeight}px`,
    ),
  );
  observer.observe(area);
  cleanup(() => observer.disconnect());
});
const positions = new Map<string, { top: number; left: number }>();
watch(revealChanges, async (value) => {
  if (!value) return;
  const record = recordTabs.value.find((t) => t.key === value.page);
  if (record) showRecord(record.channel, record.id, record.entity);
  else goPage(value.page);
  await nextTick();
  [...document.querySelectorAll<HTMLInputElement>(".date-invalid input")]
    .find((el) => el.getClientRects().length)
    ?.focus();
});
const viewKey = computed(
  () =>
    `${session.value?.tenant_id}:${session.value?.principal_id}:${activeEntity.value}:${activeEditor.value || activeRecord.value || page.value}`,
);
watch(
  viewKey,
  async (key, old) => {
    if (workArea.value)
      positions.set(old, {
        top: workArea.value.scrollTop,
        left: workArea.value.scrollLeft,
      });
    await nextTick();
    const position = positions.get(key);
    workArea.value?.scrollTo({
      top: position?.top || 0,
      left: position?.left || 0,
    });
    tabBar.value
      ?.querySelector('[aria-current="page"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  },
  { flush: "pre" },
);
import { loadOptions as loadPagedOptions } from "./business";
import { itemForPage, widthForPage } from "./navigation";
const WarehousesPage = defineAsyncComponent(
  () => import("./WarehousesPage.vue"),
);
const ProductFieldSettings = defineAsyncComponent(
  () => import("./MasterConfiguration.vue"),
);
const AdmissionPage = defineAsyncComponent(() => import("./AdmissionPage.vue"));
const CatalogDictionaryPage = defineAsyncComponent(
  () => import("./CatalogDictionaryPage.vue"),
);
const CatalogPage = defineAsyncComponent(() => import("./CatalogPage.vue"));
const ProductMasterWorkspace = defineAsyncComponent(
  () => import("./ProductMasterWorkspace.vue"),
);
import PurchasingPage from "./PurchasingPage.vue";
import LedgerPage from "./LedgerPage.vue";
import SalesPage from "./SalesPage.vue";
import ReceivablesPage from "./ReceivablesPage.vue";
import FinancePage from "./FinancePage.vue";
const QualityPage = defineAsyncComponent(
  () => import("./QualityWorkspace.vue"),
);
const QualityRecords = defineAsyncComponent(
  () => import("./QualityRecords.vue"),
);
const ManagementRecords = defineAsyncComponent(
  () => import("./ManagementRecords.vue"),
);
const EnterpriseSettings = defineAsyncComponent(
  () => import("./EnterpriseSettings.vue"),
);
const MessagesPage = defineAsyncComponent(() => import("./MessagesPage.vue"));
const LocationMovesPage = defineAsyncComponent(
  () => import("./LocationMovesPage.vue"),
);
const TransfersPage = defineAsyncComponent(() => import("./TransfersPage.vue"));
const CountsPage = defineAsyncComponent(() => import("./CountsPage.vue"));
const OpeningsPage = defineAsyncComponent(() => import("./OpeningsPage.vue"));
const RetailPage = defineAsyncComponent(() => import("./RetailPage.vue"));
const ReturnsPage = defineAsyncComponent(() => import("./ReturnsPage.vue"));
import {
  ApiError,
  loadSession,
  refreshSession,
  request,
  session,
  type Named,
  type Session,
} from "./api";
interface Member {
  principal_id: string;
  display_name: string;
  role_codes: string[];
  active: boolean;
  entity_ids: string[];
  version: string;
}
interface MemberPage {
  items: Member[];
  roles: { code: string; name: string }[];
  entities: Named[];
}
interface Stock {
  balance_id: string;
  location_id: string;
  location_code: string;
  location_name: string;
  lot_id: string | null;
  on_hand: string;
  reserved: string;
  blocked: string;
  available: string;
  eligible: boolean;
  ineligibility_codes: string[];
}
const returnLaunch = ref<{ posting_id: string; entity_id: string } | null>(
  null,
);
const workLaunch = ref<WorkQueueItem | null>(null);
const workOrigin = ref<WorkbenchVisit | null>(null);
const currentWorkOrigin = computed(() => {
  if (activeEditor.value) return null;
  if (activeRecord.value)
    return recordTabs.value.find((t) => t.key === activeRecord.value)?.origin;
  return page.value === workLaunch.value?.page ? workOrigin.value : null;
});
watch(
  workbenchReturn,
  (target) => {
    if (target) selectHomeView(target.visit.view);
  },
  { flush: "sync" },
);
const workFilters = ref<WorkQueueFilters | null>(null);
const recoveryVersion = ref(0);
async function recoveredSubmission() {
  if (openedEditors.value.length) {
    try {
      if (await refreshConvertedDrafts()) return;
    } catch (e) {
      await failure(e);
    }
  }
  workLaunch.value = null;
  workFilters.value = null;
  page.value = "overview";
  recoveryVersion.value++;
}
const purchaseLaunch = ref<{
  id: string;
  entity_id: string;
  sale_id: string;
} | null>(null);
const sourceSale = ref<{ id: string; entity_id: string } | null>(null);
function openReplenishmentPurchase(source: {
  id: string;
  entity_id: string;
  sale_id: string;
}) {
  workLaunch.value = null;
  purchaseLaunch.value = source;
  page.value = "purchasing";
}
function openSourceSale(source: { id: string; entity_id: string }) {
  workLaunch.value = null;
  sourceSale.value = source;
  page.value = "selling";
}
function openWorkItem(visit: WorkbenchVisit) {
  const { item } = visit;
  if (
    item.entity_id !== activeEntity.value ||
    !session.value?.permissions.includes(
      itemForPage(item.page)?.permission || "",
    )
  )
    return;
  returnLaunch.value = null;
  purchaseLaunch.value = null;
  sourceSale.value = null;
  if (item.page === "purchasing" || item.page === "selling") {
    workLaunch.value = null;
    workOrigin.value = null;
    showRecord(
      item.page === "purchasing" ? "purchase" : "sale",
      item.target_id,
      item.entity_id,
      item.number,
      visit,
    );
    return;
  }
  workOrigin.value = visit;
  workLaunch.value = item;
  goPage(item.page);
}
function openRetailReturn(source: { posting_id: string; entity_id: string }) {
  returnLaunch.value = source;
  page.value = "returns";
}
watch(
  () => session.value?.tenant_id,
  (value) => {
    if (!value) resetWorkspace();
  },
);
const loginError = new URLSearchParams(location.search).has("login_error");
const loading = ref(true),
  busy = ref(false),
  error = ref(""),
  dialog = ref(false);
watch(page, (value) => {
  if (value !== "purchasing") purchaseLaunch.value = null;
  if (value !== "selling") sourceSale.value = null;
  if (value !== "returns") returnLaunch.value = null;
  if (value !== workLaunch.value?.page) {
    workLaunch.value = null;
    workOrigin.value = null;
  }
  void nextTick(() => window.scrollTo({ top: 0 }));
});
// Enter the branded, same-origin login form directly. A failed callback stays
// on the recovery screen, so missing membership cannot create a redirect loop.
watch([loading, session, error], () => {
  if (!loading.value && !session.value && !error.value && !loginError) {
    if (location.pathname.startsWith("/workspace/"))
      sessionStorage.setItem(
        "medinv.returnPath",
        location.pathname + location.search,
      );
    window.location.replace("/auth/login");
  }
});
const members = ref<MemberPage>({ items: [], roles: [], entities: [] }),
  editing = ref<Member | null>(null);
const entity = ref(""),
  warehouse = ref(""),
  sku = ref(""),
  stock = ref<Stock[]>([]),
  inventoryLoading = ref(false),
  searched = ref(false);
const options = ref<{ warehouses: Named[]; skus: Named[] }>({
  warehouses: [],
  skus: [],
});
const canManage = computed(() =>
  session.value?.permissions.includes("members.manage"),
);
const canReadMembers = computed(() =>
  session.value?.permissions.includes("members.read"),
);
const owner = computed(() => session.value?.role_codes.includes("OWNER"));
const availableRoles = computed(() =>
  members.value.roles.filter(
    (r) => owner.value || !["OWNER", "ADMIN"].includes(r.code),
  ),
);
const roleNames: Record<string, string> = {
  OWNER: "企业所有者",
  ADMIN: "企业管理员",
  OPERATOR: "业务操作员",
  VIEWER: "只读成员",
  QUALITY: "质量审核员",
  FINANCE: "财务人员",
  PURCHASER: "采购员",
  WAREHOUSE: "仓储员",
  SALES: "销售员",
  CASHIER: "收银员",
};
const pageTitle = computed(() => itemForPage(page.value)?.name || "企业工作台");

const messages: Record<string, string> = {
  EXPIRED: "批次已过期",
  IN_TRANSIT: "调拨在途，不可销售",
  COUNT_FROZEN: "批次盘点冻结中",
  SKU_BLOCKED: "商品已冻结",
  LOT_BLOCKED: "批次已冻结",
  QUALITY_NOT_QUALIFIED: "非合格库存",
  EXPIRY_UNRESOLVED: "效期待确认",
  LOT_REQUIRED: "缺少批次",
  ENTITY_SUSPENDED: "主体已停用",
  PRODUCT_UNAPPROVED: "商品待审核",
  RESTRICTED_PRODUCT: "受限类别未开放",
  SERIAL_ALLOCATION_NOT_ENABLED: "序列号分配未开放",
};
let timer: ReturnType<typeof setInterval> | undefined,
  epoch = 0;
function resetTenant() {
  resetWorkspace();
  positions.clear();
  workOrigin.value = null;
  purchaseLaunch.value = null;
  sourceSale.value = null;
  workLaunch.value = null;
  workFilters.value = null;
  returnLaunch.value = null;
  members.value = { items: [], roles: [], entities: [] };
  entity.value = activeEntity.value;
  warehouse.value = "";
  sku.value = "";
  stock.value = [];
  searched.value = false;
  options.value = { warehouses: [], skus: [] };
  dialog.value = false;
  editing.value = null;
  epoch++;
}
async function failure(err: unknown) {
  if (err instanceof ApiError && err.code === "CONTEXT_STALE") {
    try {
      await loadSession();
      resetTenant();
      initializeWorkspace(true);
      ElMessage.warning("企业上下文已更新，请重新操作");
    } catch {
      session.value = null;
    }
    return;
  }
  ElMessage.error(err instanceof Error ? err.message : "请求未完成");
}
async function bootstrap() {
  loading.value = true;
  error.value = "";
  try {
    await loadSession();
    resetTenant();
    const returnPath = sessionStorage.getItem("medinv.returnPath");
    if (returnPath?.startsWith("/workspace/")) {
      history.replaceState({}, "", returnPath);
      sessionStorage.removeItem("medinv.returnPath");
    }
    initializeWorkspace();
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 401))
      error.value = "暂时无法连接服务，请确认后端已启动";
  } finally {
    loading.value = false;
  }
}
async function changeEntity(id: string) {
  if (id === activeEntity.value || !(await guardWorkspaceLeave())) return;
  if (!selectEntity(id)) return;
  const tabs = [...moduleTabs.value],
    current = page.value;
  resetTenant();
  initializeWorkspace(true);
  moduleTabs.value = tabs;
  goPage(current);
  if (current === "inventory") await loadOptions();
  ElMessage.success("已切换经营主体，已保存的单据草稿可从草稿列表继续");
}
watch(
  activeEntity,
  (value) => {
    entity.value = value;
    workLaunch.value = null;
    workOrigin.value = null;
    workFilters.value = null;
    purchaseLaunch.value = null;
    sourceSale.value = null;
    returnLaunch.value = null;
    warehouse.value = "";
    sku.value = "";
    stock.value = [];
    searched.value = false;
    if (page.value === "inventory") void loadOptions();
  },
  { flush: "sync" },
);
async function changeTenant(id: string) {
  if (!(await guardWorkspaceLeave())) return;
  busy.value = true;
  try {
    session.value = await request<Session>("/auth/tenant", {
      method: "POST",
      body: JSON.stringify({ tenant_id: id }),
    });
    resetTenant();
    initializeWorkspace(true);
    ElMessage.success("已切换企业");
  } catch (err) {
    await failure(err);
  } finally {
    busy.value = false;
  }
}
async function logout() {
  if (!(await guardWorkspaceLeave())) return;
  busy.value = true;
  try {
    await request("/auth/logout", { method: "POST" });
    session.value = null;
    resetTenant();
  } catch (err) {
    await failure(err);
  } finally {
    busy.value = false;
  }
}
async function loadMembers() {
  busy.value = true;
  const context = `${session.value?.tenant_id}:${session.value?.context_generation}`;
  try {
    const result = await request<MemberPage>("/control/members");
    if (
      context ===
      `${session.value?.tenant_id}:${session.value?.context_generation}`
    )
      members.value = result;
  } catch (err) {
    await failure(err);
  } finally {
    busy.value = false;
  }
}
function editable(row: Member) {
  return (
    canManage.value &&
    row.principal_id !== session.value?.principal_id &&
    (owner.value || !row.role_codes.some((r) => ["OWNER", "ADMIN"].includes(r)))
  );
}
function edit(row: Member) {
  editing.value = {
    ...row,
    role_codes: [...row.role_codes],
    entity_ids: [...row.entity_ids],
  };
  dialog.value = true;
}
async function saveMember() {
  if (!editing.value) return;
  busy.value = true;
  try {
    await request(`/control/members/${editing.value.principal_id}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_version: editing.value.version,
        role_codes: editing.value.role_codes,
        entity_ids: editing.value.entity_ids,
        active: editing.value.active,
      }),
    });
    dialog.value = false;
    ElMessage.success("权限已保存，该成员需重新登录");
    await loadMembers();
  } catch (err) {
    await failure(err);
  } finally {
    busy.value = false;
  }
}
async function loadOptions() {
  const current = ++epoch;
  stock.value = [];
  searched.value = false;
  if (!entity.value) return;
  inventoryLoading.value = true;
  try {
    const result = await loadPagedOptions<{
      warehouses: Named[];
      skus: Named[];
    }>(`/api/v1/inventory/options?entity_id=${entity.value}`);
    if (current !== epoch) return;
    options.value = result;
    warehouse.value = result.warehouses[0]?.id || "";
    sku.value = result.skus[0]?.id || "";
    inventoryLoading.value = false;
    if (warehouse.value && sku.value) await searchStock();
  } catch (err) {
    await failure(err);
  } finally {
    if (current === epoch) inventoryLoading.value = false;
  }
}
async function searchStock() {
  if (!entity.value || !warehouse.value || !sku.value) return;
  const current = ++epoch;
  inventoryLoading.value = true;
  try {
    const data = await request<{ items: Stock[] }>(
      `/api/v1/inventory/availability?${new URLSearchParams({ entity_id: entity.value, warehouse_id: warehouse.value, sku_id: sku.value })}`,
    );
    if (current === epoch) {
      stock.value = data.items;
      searched.value = true;
    }
  } catch (err) {
    await failure(err);
  } finally {
    if (current === epoch) inventoryLoading.value = false;
  }
}
function quantity(value: string) {
  return value.includes(".")
    ? value.replace(/0+$/, "").replace(/\.$/, "")
    : value;
}
watch(page, (value) => {
  if (value === "members") void loadMembers();
  if (value === "inventory") void loadOptions();
});
onMounted(async () => {
  await bootstrap();
  timer = setInterval(async () => {
    if (session.value) {
      try {
        await refreshSession();
      } catch (err) {
        if (!(err instanceof ApiError && err.code === "SESSION_REFRESH_BUSY"))
          await failure(err);
      }
    }
  }, 60000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div v-if="loading" class="boot">
    <div class="brand-mark">药</div>
    <p>正在连接企业工作空间…</p>
  </div>
  <div v-else-if="!session" class="login-page">
    <section class="login-story">
      <div class="wordmark">
        <span class="brand-mark">药</span><strong>药械云</strong
        ><span class="brand-caption">企业进销存</span>
      </div>
      <div class="story-body">
        <span class="eyebrow">MEDICAL SUPPLY WORKSPACE</span>
        <h1>每一份库存，<br />都在清晰的管理之中。</h1>
        <p>
          连接企业、成员与业务数据，<br />让药械经营从一个有序的工作空间开始。
        </p>
        <div class="flow-visual">
          <div class="flow-label">企业工作空间</div>
          <div class="flow-branches">
            <span>成员与角色</span><span>主体与仓库</span
            ><span>批次与库存</span>
          </div>
          <div class="flow-note"><i></i>在当前企业范围内协同工作</div>
        </div>
      </div>
      <div class="story-footer">药品 · 医疗器械<span>多企业 SaaS</span></div>
    </section>
    <section class="login-panel">
      <div class="login-card">
        <span class="env-tag">本机开发演示</span>
        <h2>欢迎进入药械云</h2>
        <p>使用企业账号登录，进入您的工作台。</p>
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
        /><el-alert
          v-if="!error && loginError"
          title="登录未完成，请重新尝试或检查企业成员身份"
          type="warning"
          :closable="false"
        /><a class="login-button" href="/auth/login"
          >使用企业账号登录<span>→</span></a
        >
        <div class="login-hint">
          在本站登录页填写企业账号与密码。<br />登录后进入您有权限的企业工作台。
        </div>
        <div class="login-divider"></div>
        <div class="login-foot">
          <b>首次使用？</b><span>请联系企业管理员开通成员权限。</span>
        </div>
        <el-button v-if="error" text @click="bootstrap">重新连接</el-button>
      </div>
      <p class="panel-footer">身份验证 · 企业隔离 · 权限控制</p>
    </section>
  </div>
  <div v-else class="app-shell" :data-module="activeEditor ? 'editor' : page">
    <header class="workspace-chrome">
      <div class="workspace-masthead">
        <div class="wordmark">
          <span class="brand-mark">药</span>
          <div><strong>药械云</strong><small>企业进销存</small></div>
        </div>
        <button
          v-if="appearance.layout === 'sidebar'"
          class="chrome-collapse"
          :aria-label="appearance.sidebarCollapsed ? '展开导航' : '收起导航'"
          :aria-expanded="!appearance.sidebarCollapsed"
          @click="appearance.sidebarCollapsed = !appearance.sidebarCollapsed"
        >
          ☰
        </button>
        <BusinessNavigation
          :model-value="page"
          @update:model-value="goPage"
          :key="`${session.tenant_id}:${session.principal_id}:${session.context_generation}`"
          :permissions="session.permissions"
          :disabled="busy"
        />
        <div class="account">
          <button
            class="chrome-settings"
            aria-label="界面与操作设置"
            @click="goPage('appearance')"
          >
            ◧
          </button>
          <el-select
            aria-label="切换企业"
            :model-value="session.tenant_id"
            :disabled="busy"
            @change="changeTenant"
          >
            <el-option
              v-for="item in session.tenants"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
          <el-select
            aria-label="切换经营主体"
            :model-value="activeEntity"
            :disabled="busy"
            @change="changeEntity"
            placeholder="未分配经营主体"
          >
            <el-option
              v-for="item in session.entities"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
          <div class="avatar">{{ session.display_name.slice(0, 1) }}</div>
          <span class="account-name">{{ session.display_name }}</span>
          <button class="logout" :disabled="busy" @click="logout">退出</button>
        </div>
      </div>
    </header>
    <CloseChangesDialog />
    <nav ref="tabBar" class="work-tabs" aria-label="工作标签">
      <div
        v-for="tab in moduleTabs"
        :key="tab"
        class="editor-tab module-tab"
        :class="{ active: !activeEditor && !activeRecord && page === tab }"
      >
        <button
          :aria-current="
            !activeEditor && !activeRecord && page === tab ? 'page' : undefined
          "
          :title="itemForPage(tab)?.name || '工作台'"
          @click="goPage(tab)"
        >
          {{ itemForPage(tab)?.name || "工作台" }}
          <span v-if="hasCatalogChanges(tab)" aria-label="未保存"> ●</span>
        </button>
        <button
          v-if="tab !== 'overview'"
          :aria-label="'关闭' + (itemForPage(tab)?.name || tab)"
          title="关闭标签"
          @click="closeModuleTab(tab).catch(failure)"
        >
          ×
        </button>
      </div>
      <div
        v-for="tab in recordTabs"
        :key="tab.key"
        class="editor-tab record-tab"
        :class="{ active: activeRecord === tab.key }"
      >
        <button
          :aria-current="activeRecord === tab.key ? 'page' : undefined"
          :title="tab.title + ' · ' + tab.id"
          @click="showRecord(tab.channel, tab.id, tab.entity)"
        >
          {{ tab.title
          }}<span v-if="hasCatalogChanges(tab.key)" aria-label="未保存">
            ●</span
          >
        </button>
        <button
          :aria-label="'关闭' + tab.title"
          @click="closeRecordTab(tab.key).catch(failure)"
        >
          ×
        </button>
      </div>
      <div
        v-for="id in openedEditors"
        :key="id"
        class="editor-tab"
        :class="{ active: activeEditor === id }"
      >
        <button
          :aria-current="activeEditor === id ? 'page' : undefined"
          :title="editors[id]?.record.title || '单据草稿 ' + id"
          @click="openDraft(id)"
        >
          {{ editors[id]?.record.source_order_id ? "修改" : "新建"
          }}{{ editors[id]?.record.channel === "purchase" ? "采购" : "销售" }}单
          · {{ id.slice(0, 4)
          }}<span
            v-if="editors[id] && isDirty(editors[id]!)"
            aria-label="未保存"
          >
            ●</span
          >
        </button>
        <button
          :aria-label="'关闭单据 ' + id.slice(0, 4)"
          @click="closeEditor(id).catch(failure)"
        >
          ×
        </button>
      </div>
    </nav>
    <div ref="workArea" class="main-shell">
      <main
        class="workspace-content"
        :data-width="activeEditor || activeRecord ? 'wide' : widthForPage(page)"
      >
        <CommandRecovery @resolved="recoveredSubmission" @failure="failure" />
        <p v-if="editorError" role="alert" class="editor-route-error">
          {{ editorError }}
        </p>
        <p v-if="editorLoading" role="status">正在打开单据…</p>
        <div
          v-if="currentWorkOrigin"
          class="business-toolbar"
          aria-label="原单待办来源"
        >
          <el-button @click="returnToWorkbench(currentWorkOrigin)">{{
            currentWorkOrigin.view === "risk" ? "返回风险事项" : "返回业务待办"
          }}</el-button>
          <span
            >来自{{
              currentWorkOrigin.view === "risk" ? "风险事项" : "业务待办"
            }}
            ·
            {{
              currentWorkOrigin.item.related_number ||
              currentWorkOrigin.item.number
            }}</span
          >
        </div>
        <DocumentEditor
          v-for="id in openEditorIds"
          v-show="activeEditor === id"
          :key="id"
          :id="id"
          :active="activeEditor === id"
          @failure="failure"
        />
        <DocumentRecord
          v-for="tab in recordTabs"
          :key="tab.key"
          v-show="activeRecord === tab.key"
          :identity="tab.key"
          :id="tab.id"
          :entity="tab.entity"
          :channel="tab.channel"
          :active="activeRecord === tab.key"
          @failure="failure"
          @sale="openSourceSale"
          @purchase="openReplenishmentPurchase"
        />
        <div
          v-show="!activeEditor && !activeRecord"
          class="module-content"
          :key="`${session.tenant_id}:${session.context_generation}:${activeEntity}`"
        >
          <h1 class="visually-hidden">{{ pageTitle }}</h1>
          <WorkDashboard
            v-if="moduleTabs.includes('overview')"
            v-show="page === 'overview'"
            :active="page === 'overview' && !activeEditor && !activeRecord"
            v-model="workFilters"
            @open="openWorkItem"
          />
          <EnterpriseSettings
            v-if="moduleTabs.includes('enterprise-settings')"
            v-show="page === 'enterprise-settings'"
            @failure="failure"
          />
          <template
            v-for="item in [
              { page: 'staff', kind: 'staff' },
              { page: 'quality-ledgers', kind: 'maintenance' },
              { page: 'approval-design', kind: 'approval_design' },
            ] as const"
            :key="item.page"
            ><ManagementRecords
              :active="page === item.page && !activeEditor && !activeRecord"
              v-if="moduleTabs.includes(item.page)"
              v-show="page === item.page"
              :kind="item.kind"
              :initial-target="
                referenceNavigation?.page === item.page
                  ? referenceNavigation
                  : undefined
              "
              @failure="failure"
          /></template>
          <MessagesPage
            v-if="moduleTabs.includes('messages')"
            v-show="page === 'messages'"
            :active="page === 'messages'"
            @failure="failure"
            @open="
              (item) =>
                openWorkItem({
                  item,
                  index: 0,
                  view: 'operations',
                  filters: {
                    entity_id: activeEntity,
                    kind: item.kind,
                    search: '',
                    offset: 0,
                  },
                })
            "
          />
          <QualityRecords
            v-if="page === 'quality-records'"
            @failure="failure"
          />
          <AppearanceSettings
            v-if="moduleTabs.includes('appearance')"
            v-show="page === 'appearance'"
          />
          <div v-show="page === 'productFields'">
            <ProductFieldSettings
              v-if="moduleTabs.includes('productFields')"
              :key="`${session.tenant_id}:${session.context_generation}`"
              :active="page === 'productFields'"
              @failure="failure"
            />
          </div>
          <AdmissionPage
            v-if="moduleTabs.includes('admission')"
            v-show="page === 'admission'"
            @failure="failure"
          />
          <div v-show="page === 'dictionaries'">
            <CatalogDictionaryPage
              :active="
                page === 'dictionaries' && !activeEditor && !activeRecord
              "
              v-if="moduleTabs.includes('dictionaries')"
              :key="`${session.tenant_id}:${session.context_generation}`"
              @failure="failure"
            />
          </div>
          <div
            v-for="kind in ['products', 'parties'] as const"
            :key="kind"
            v-show="page === kind"
          >
            <ProductMasterWorkspace
              :active="page === kind && !activeEditor && !activeRecord"
              v-if="kind === 'products' && moduleTabs.includes(kind)"
              :initial-target="
                referenceNavigation?.page === kind
                  ? referenceNavigation
                  : undefined
              "
              :key="`${session.tenant_id}:${session.context_generation}:master`"
              @failure="failure"
            />
            <CatalogPage
              :active="page === kind && !activeEditor && !activeRecord"
              v-if="kind === 'parties' && moduleTabs.includes(kind)"
              :key="`${session.tenant_id}:${session.context_generation}:${kind}`"
              :kind="kind"
              :initial-target="
                referenceNavigation?.page === kind
                  ? referenceNavigation
                  : undefined
              "
              @failure="failure"
            />
          </div>
          <div
            v-if="purchaseLaunch && page === 'purchasing'"
            class="business-toolbar"
          >
            <el-button
              @click="
                openSourceSale({
                  id: purchaseLaunch.sale_id,
                  entity_id: purchaseLaunch.entity_id,
                })
              "
              >返回原销售单</el-button
            >
            <span class="form-note"
              >补货采购沿用原审核、收货和入库流程，入库后回原销售单分配发货。</span
            >
          </div>
          <div
            v-if="moduleTabs.includes('purchasing')"
            v-show="page === 'purchasing'"
          >
            <PurchasingPage
              :key="`${session.tenant_id}:${session.context_generation}:${purchaseLaunch?.id || ''}`"
              :initial-id="
                purchaseLaunch?.id ||
                (workLaunch?.page === 'purchasing'
                  ? workLaunch.target_id
                  : undefined)
              "
              :initial-entity-id="
                purchaseLaunch?.entity_id ||
                (workLaunch?.page === 'purchasing'
                  ? workLaunch.entity_id
                  : undefined)
              "
              @failure="failure"
              @sale="openSourceSale"
            />
          </div>
          <div
            v-if="moduleTabs.includes('selling')"
            v-show="page === 'selling'"
          >
            <SalesPage
              :key="`${session.tenant_id}:${session.context_generation}:${sourceSale?.id || ''}`"
              :initial-id="
                sourceSale?.id ||
                (workLaunch?.page === 'selling'
                  ? workLaunch.target_id
                  : undefined)
              "
              :initial-entity-id="
                sourceSale?.entity_id ||
                (workLaunch?.page === 'selling'
                  ? workLaunch.entity_id
                  : undefined)
              "
              @failure="failure"
              @purchase="openReplenishmentPurchase"
            />
          </div>
          <ReceivablesPage
            v-if="page === 'receivables'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            @failure="failure"
          />
          <FinancePage
            v-if="page === 'finance'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            @failure="failure"
          />
          <OpeningsPage
            v-if="page === 'openings'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            :initial-id="workLaunch?.target_id"
            :initial-entity-id="workLaunch?.entity_id"
            @failure="failure"
          />
          <QualityPage
            v-if="page === 'quality'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            :initial-id="workLaunch?.target_id"
            :initial-entity-id="workLaunch?.entity_id"
            @failure="failure"
          />
          <WarehousesPage
            :active="page === 'warehouses' && !activeEditor && !activeRecord"
            v-if="moduleTabs.includes('warehouses')"
            v-show="page === 'warehouses'"
            :key="`${session?.tenant_id}:${session?.context_generation}`"
            @failure="failure"
          />
          <LocationMovesPage
            v-if="page === 'location-moves'"
            @failure="failure"
          />
          <TransfersPage
            v-if="page === 'transfers'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            :initial-id="workLaunch?.target_id"
            :initial-loss-id="
              workLaunch?.kind.startsWith('transfer_loss_')
                ? workLaunch.item_id
                : undefined
            "
            :initial-entity-id="workLaunch?.entity_id"
            @failure="failure"
          />
          <CountsPage
            v-if="page === 'counts'"
            :key="`${session?.tenant_id}:${session?.context_generation}`"
            :initial-id="workLaunch?.target_id"
            :initial-entity-id="workLaunch?.entity_id"
            @failure="failure"
          />
          <RetailPage
            @return-sale="openRetailReturn"
            v-if="page === 'retail'"
            :key="session?.tenant_id + '-' + session?.context_generation"
            :initial-id="workLaunch?.target_id"
            :initial-entity-id="workLaunch?.entity_id"
            @failure="failure"
          />
          <ReturnsPage
            :initial-id="workLaunch?.target_id"
            :initial-source-id="returnLaunch?.posting_id"
            :initial-entity-id="
              workLaunch?.entity_id || returnLaunch?.entity_id
            "
            v-if="page === 'returns'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            @failure="failure"
          />
          <LedgerPage
            v-if="page === 'ledger'"
            :key="`${session.tenant_id}:${session.context_generation}`"
            @failure="failure"
          />
          <section v-if="page === 'members'" class="panel" v-loading="busy">
            <div class="panel-heading">
              <h3>
                企业成员<span class="count">{{ members.items.length }}</span>
              </h3>
              <el-button @click="loadMembers">刷新列表</el-button>
            </div>
            <el-table :data="members.items" empty-text="当前企业暂无成员"
              ><el-table-column label="成员" min-width="180"
                ><template #default="{ row }"
                  ><div class="member-name">
                    <span class="avatar small">{{
                      row.display_name.slice(0, 1)
                    }}</span
                    ><b>{{ row.display_name }}</b
                    ><el-tag
                      v-if="row.principal_id === session.principal_id"
                      size="small"
                      type="info"
                      >我</el-tag
                    >
                  </div></template
                ></el-table-column
              ><el-table-column label="角色" min-width="160"
                ><template #default="{ row }">{{
                  row.role_codes
                    .map((r: string) => roleNames[r] || r)
                    .join(" / ")
                }}</template></el-table-column
              ><el-table-column label="主体范围" min-width="130"
                ><template #default="{ row }"
                  >{{ row.entity_ids.length }} 个经营主体</template
                ></el-table-column
              ><el-table-column label="状态" width="95"
                ><template #default="{ row }"
                  ><el-tag :type="row.active ? 'success' : 'info'">{{
                    row.active ? "已启用" : "已停用"
                  }}</el-tag></template
                ></el-table-column
              ><el-table-column label="操作" width="110"
                ><template #default="{ row }"
                  ><el-button
                    v-if="editable(row)"
                    link
                    type="primary"
                    @click="edit(row)"
                    >编辑权限</el-button
                  ><span v-else class="muted">—</span></template
                ></el-table-column
              ></el-table
            >
          </section>
          <section
            v-if="page === 'inventory'"
            class="panel"
            v-loading="inventoryLoading"
          >
            <div class="filters">
              <label
                >仓库<el-select v-model="warehouse" aria-label="库存仓库"
                  ><el-option
                    v-for="item in options.warehouses"
                    :key="item.id"
                    :label="item.name"
                    :value="item.id" /></el-select></label
              ><label
                >商品<el-select v-model="sku" aria-label="库存商品" filterable
                  ><el-option
                    v-for="item in options.skus"
                    :key="item.id"
                    :label="item.name"
                    :value="item.id" /></el-select></label
              ><el-button
                type="primary"
                :disabled="!warehouse || !sku"
                @click="searchStock"
                >查询库存</el-button
              >
            </div>
            <el-table
              :data="stock"
              :empty-text="searched ? '没有符合条件的库存' : '请选择仓库与商品'"
              ><el-table-column label="库存记录" min-width="165"
                ><template #default="{ row }"
                  ><span class="mono">{{
                    row.balance_id.slice(0, 8).toUpperCase()
                  }}</span
                  ><small class="cell-sub">{{
                    row.lot_id
                      ? "批次 " + row.lot_id.slice(0, 8).toUpperCase()
                      : "无批次"
                  }}</small></template
                ></el-table-column
              ><el-table-column label="库位" min-width="165"
                ><template #default="{ row }"
                  >{{ row.location_code
                  }}<small
                    v-if="row.location_name !== row.location_code"
                    class="cell-sub"
                    >{{ row.location_name }}</small
                  ></template
                ></el-table-column
              ><el-table-column label="账面数量" align="right"
                ><template #default="{ row }">{{
                  quantity(row.on_hand)
                }}</template></el-table-column
              ><el-table-column label="已预占" align="right"
                ><template #default="{ row }">{{
                  quantity(row.reserved)
                }}</template></el-table-column
              ><el-table-column label="冻结数量" align="right"
                ><template #default="{ row }">{{
                  quantity(row.blocked)
                }}</template></el-table-column
              ><el-table-column label="可用数量" align="right"
                ><template #default="{ row }"
                  ><b class="available">{{
                    quantity(row.available)
                  }}</b></template
                ></el-table-column
              ><el-table-column label="可分配状态" min-width="160"
                ><template #default="{ row }"
                  ><el-tag v-if="row.eligible" type="success" effect="plain"
                    >可分配</el-tag
                  ><span v-else class="warning-text">{{
                    row.ineligibility_codes
                      .map((c: string) => messages[c] || c)
                      .join("、")
                  }}</span></template
                ></el-table-column
              ></el-table
            >
            <div class="table-note">
              库存数据为查询时点的快照；实际分配以预占结果为准。
            </div>
          </section>
        </div>
      </main>
    </div>
    <el-dialog v-model="dialog" title="编辑成员权限" width="480px"
      ><el-form v-if="editing" label-position="top"
        ><el-form-item label="成员"
          ><el-input
            :model-value="editing.display_name"
            disabled /></el-form-item
        ><el-form-item label="角色"
          ><el-select v-model="editing.role_codes" multiple style="width: 100%"
            ><el-option
              v-for="role in availableRoles"
              :key="role.code"
              :label="role.name"
              :value="role.code" /></el-select></el-form-item
        ><el-form-item label="授权经营主体"
          ><el-select v-model="editing.entity_ids" multiple style="width: 100%"
            ><el-option
              v-for="item in members.entities"
              :key="item.id"
              :label="item.name"
              :value="item.id" /></el-select></el-form-item
        ><el-form-item label="成员状态"
          ><el-switch
            v-model="editing.active"
            active-text="启用"
            inactive-text="停用" /></el-form-item
        ><el-alert
          title="保存后该成员的现有会话将失效，需要重新登录。"
          type="info"
          :closable="false" /></el-form
      ><template #footer
        ><el-button @click="dialog = false">取消</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="!editing?.role_codes.length"
          @click="saveMember"
          >保存权限</el-button
        ></template
      ></el-dialog
    >
  </div>
</template>
