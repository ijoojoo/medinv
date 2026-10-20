<script setup lang="ts">
import { onMounted, onUnmounted, provide, ref, watch } from "vue";
import { CHANGE_SCOPE, hasCatalogChanges } from "./catalogChanges";
import { loadOptions, type PurchaseOptions } from "./business";
import {
  goPage,
  moduleFor,
  recordSaved,
  recordTabs,
  recordNavigation,
} from "./workspace";
import type { EntryChannel } from "./documentEntry";
import PurchaseDetail from "./PurchaseDetail.vue";
import SalesDetail from "./SalesDetail.vue";
const props = defineProps<{
  identity: string;
  id: string;
  entity: string;
  channel: EntryChannel;
  active: boolean;
}>();
const emit = defineEmits<{
  failure: [error: unknown];
  sale: [source: { id: string; entity_id: string }];
  purchase: [source: { id: string; entity_id: string; sale_id: string }];
}>();
provide(CHANGE_SCOPE, props.identity);
const options = ref<PurchaseOptions | null>(null),
  error = ref(false),
  revision = ref(0),
  needsRefresh = ref(false);
let alive = true;
onUnmounted(() => {
  alive = false;
});
async function load() {
  error.value = false;
  try {
    const result = await loadOptions<PurchaseOptions>(
      `/api/v1/purchasing/options?entity_id=${props.entity}&include_skus=false&include_parties=false`,
    );
    if (alive) options.value = result;
  } catch (e) {
    if (alive) {
      error.value = true;
      emit("failure", e);
    }
  }
}
function updateTitle(number: string) {
  const tab = recordTabs.value.find((t) => t.key === props.identity);
  if (tab) tab.title = number;
}
watch(recordSaved, (target) => {
  if (target?.id === props.id && target.channel === props.channel) {
    needsRefresh.value = true;
    refreshIfSafe();
  }
});
function refreshIfSafe() {
  if (!props.active || !needsRefresh.value || hasCatalogChanges(props.identity))
    return;
  revision.value++;
  needsRefresh.value = false;
}
watch(
  [() => props.active, recordNavigation],
  ([active, target], [wasActive, previous]) => {
    if (
      active &&
      (!wasActive ||
        (target !== previous &&
          target?.id === props.id &&
          target.channel === props.channel))
    ) {
      needsRefresh.value = true;
      refreshIfSafe();
    }
  },
);
onMounted(() => {
  if (props.channel === "purchase") void load();
});
</script>
<template>
  <section class="module-content">
    <p v-if="active && needsRefresh" role="status" class="form-note">
      已保留当前操作内容，完成或取消后可刷新原单状态。
      <el-button :disabled="hasCatalogChanges(identity)" @click="refreshIfSafe"
        >刷新原单状态</el-button
      >
    </p>
    <div v-if="error" role="alert">
      打开单据失败。<el-button @click="load">重试</el-button>
    </div>
    <p v-else-if="channel === 'purchase' && !options" role="status">
      正在读取采购单…
    </p>
    <PurchaseDetail
      v-else-if="channel === 'purchase' && options"
      :key="revision"
      :id="id"
      :options="options"
      @identity="updateTitle"
      @back="goPage(moduleFor(channel))"
      @sale="emit('sale', $event)"
      @failure="emit('failure', $event)"
    />
    <SalesDetail
      v-else
      :key="'sale-' + revision"
      :id="id"
      @identity="updateTitle"
      @back="goPage(moduleFor(channel))"
      @purchase="emit('purchase', $event)"
      @failure="emit('failure', $event)"
    />
  </section>
</template>
