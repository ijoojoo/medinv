<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { activeEntity } from "./entityScope";
import { can, loadOptions, type PurchaseOptions } from "./business";
import {
  activeRecord,
  goPage,
  listCacheKey,
  recordNavigation,
  recordSaved,
  workspacePage,
} from "./workspace";
import { guardCatalogLeave } from "./catalogChanges";
import {
  purchaseStages,
  purchaseViewRequest,
  type PurchaseStage,
  type PurchaseView,
} from "./purchaseWorkflow";
import PurchaseWorkflowList from "./PurchaseWorkflowList.vue";
import PurchaseDetail from "./PurchaseDetail.vue";
import DraftBrowser from "./DraftBrowser.vue";
const props = defineProps<{ initialEntityId?: string; initialId?: string }>();
const emit = defineEmits<{
  failure: [unknown];
  sale: [source: { id: string; entity_id: string }];
}>();
const entity = ref(props.initialEntityId || activeEntity.value);
const selected = ref(props.initialId || ""),
  detailRefresh = ref(0),
  drafts = ref(false);
const stage = ref<PurchaseStage>("orders");
const modes = ref<Record<PurchaseStage, PurchaseView>>({
  orders: "pending",
  receiving: "pending",
  inspection: "pending",
  posting: "pending",
});
const visited = ref<string[]>([]);
const current = computed(() => `${stage.value}:${modes.value[stage.value]}`);
const options = ref<PurchaseOptions>({
  suppliers: [],
  skus: [],
  warehouses: [],
  locations: [],
});
const ready = ref(false),
  optionsError = ref(false);
const root = ref<HTMLElement>(),
  height = ref(660);
let alive = true,
  observer: ResizeObserver | undefined;
try {
  const saved = JSON.parse(
    sessionStorage.getItem(
      `${listCacheKey("purchase")}:view:${entity.value}`,
    ) || "null",
  );
  if (purchaseStages.some((s) => s.key === saved?.stage))
    stage.value = saved.stage;
  for (const s of purchaseStages)
    if (s.key !== "orders" && saved?.modes?.[s.key] === "history")
      modes.value[s.key] = "history";
} catch {
  /* The default orders tab remains available. */
}
function resize() {
  if (root.value?.offsetParent)
    height.value = Math.max(
      innerWidth < 600 ? 660 : 420,
      innerHeight -
        (root.value.getBoundingClientRect().top +
          (root.value.closest(".main-shell")?.scrollTop || 0) +
          window.scrollY) -
        16,
    );
}
function visit() {
  if (!visited.value.includes(current.value)) visited.value.push(current.value);
  try {
    sessionStorage.setItem(
      `${listCacheKey("purchase")}:view:${entity.value}`,
      JSON.stringify({ stage: stage.value, modes: modes.value }),
    );
  } catch {
    /* No business data relies on this preference. */
  }
  void nextTick(resize);
}
visit();
watch(current, visit);
function switchStage(value: PurchaseStage) {
  stage.value = value;
}
function tabKeys(event: KeyboardEvent) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const index = purchaseStages.findIndex((s) => s.key === stage.value);
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? 3
        : (index + (event.key === "ArrowRight" ? 1 : -1) + 4) % 4;
  stage.value = purchaseStages[next].key;
  void nextTick(() =>
    [
      ...(root.value?.querySelectorAll<HTMLButtonElement>(
        `[data-stage-tab="${stage.value}"]`,
      ) || []),
    ]
      .find((el) => el.offsetParent !== null)
      ?.focus(),
  );
}
watch(
  purchaseViewRequest,
  async (target) => {
    if (!target) return;
    if (selected.value && !(await guardCatalogLeave("purchasing"))) return;
    if (purchaseViewRequest.value !== target) return;
    selected.value = "";
    modes.value[target.stage] = target.view;
    stage.value = target.stage;
    drafts.value = target.drafts && can("purchase.write");
    purchaseViewRequest.value = null;
  },
  { immediate: true },
);
watch([activeRecord, workspacePage], async () => {
  if (
    activeRecord.value ||
    workspacePage.value !== "purchasing" ||
    selected.value
  )
    return;
  await nextTick();
  resize();
  root.value?.scrollIntoView({ block: "start" });
});
async function loadOptionsOnce() {
  optionsError.value = false;
  try {
    const result = await loadOptions<PurchaseOptions>(
      `/api/v1/purchasing/options?entity_id=${entity.value}&include_skus=false&include_parties=false`,
    );
    if (alive) {
      options.value = result;
      ready.value = true;
    }
  } catch (e) {
    if (alive) {
      optionsError.value = true;
      emit("failure", e);
    }
  }
}
function back() {
  selected.value = "";
  goPage("purchasing");
  void nextTick(resize);
}
watch(recordNavigation, (target) => {
  if (activeRecord.value || target?.channel !== "purchase") return;
  selected.value = target.id;
  detailRefresh.value++;
});
watch(recordSaved, (target) => {
  if (target?.channel === "purchase" && selected.value) detailRefresh.value++;
});
watch(
  () => props.initialId,
  (value) => {
    if (value) {
      selected.value = value;
      detailRefresh.value++;
    }
  },
);
onMounted(() => {
  const target = recordNavigation.value;
  if (target?.channel === "purchase" && !activeRecord.value)
    selected.value = target.id;
  void loadOptionsOnce();
  observer = new ResizeObserver(resize);
  if (root.value) observer.observe(root.value);
  window.addEventListener("resize", resize);
  resize();
});
onUnmounted(() => {
  alive = false;
  observer?.disconnect();
  window.removeEventListener("resize", resize);
});
</script>
<template>
  <PurchaseDetail
    v-if="selected && ready"
    :key="selected + detailRefresh"
    :id="selected"
    :options="options"
    @back="back"
    @failure="emit('failure', $event)"
    @sale="emit('sale', $event)"
  />
  <section
    v-show="!selected"
    ref="root"
    class="purchase-workspace"
    aria-label="采购业务工作区"
    :style="{ height: height + 'px' }"
  >
    <template v-for="key in visited" :key="`${entity}:${key}`">
      <PurchaseWorkflowList
        v-show="key === current"
        :stage="key.split(':')[0] as PurchaseStage"
        :view="key.split(':')[1] as PurchaseView"
        :entity="entity"
        :options="options"
        :active="key === current && !selected"
        @failure="emit('failure', $event)"
        @toggle-view="
          modes[stage] = modes[stage] === 'pending' ? 'history' : 'pending'
        "
      >
        <template #tabs>
          <div
            class="purchase-stage-tabs"
            role="tablist"
            aria-label="采购业务环节"
            @keydown="tabKeys"
          >
            <button
              v-for="s in purchaseStages"
              :key="s.key"
              role="tab"
              :data-stage-tab="s.key"
              :aria-selected="stage === s.key"
              :tabindex="stage === s.key ? 0 : -1"
              @click="switchStage(s.key)"
            >
              {{ s.name
              }}<small v-if="stage === s.key && modes[s.key] === 'history'"
                >记录</small
              >
            </button>
          </div>
        </template>
      </PurchaseWorkflowList>
    </template>
  </section>
  <p v-if="optionsError" role="alert">
    仓库选项加载失败。<el-button @click="loadOptionsOnce"
      >重试加载选项</el-button
    >
  </p>
  <DraftBrowser v-model="drafts" :entity="entity" channel="purchase" />
</template>
<style scoped>
.purchase-workspace {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: white;
  box-shadow: var(--ui-shadow);
}
.purchase-stage-tabs {
  display: flex;
  align-self: stretch;
  align-items: stretch;
  gap: 4px;
  min-width: 0;
}
.purchase-stage-tabs button {
  flex: none;
  min-height: 44px;
  padding: 0 14px;
  background: transparent;
  border: 0;
  border-bottom: 3px solid transparent;
  color: var(--ui-muted);
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}
.purchase-stage-tabs button[aria-selected="true"] {
  color: var(--ui-accent);
  background: var(--ui-tint);
  border-bottom-color: var(--ui-accent);
}
.purchase-stage-tabs button:hover {
  background: var(--ui-soft);
  color: var(--ui-accent);
}
.purchase-stage-tabs button:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: -3px;
}
.purchase-stage-tabs small {
  margin-left: 4px;
  font-size: 11px;
  font-weight: normal;
}
@media (max-width: 600px) {
  .purchase-stage-tabs {
    width: 100%;
    gap: 0;
    overflow-x: auto;
  }
  .purchase-stage-tabs button {
    font-size: 14px;
    flex: 1;
    padding: 0 9px;
    white-space: nowrap;
  }
}
</style>
