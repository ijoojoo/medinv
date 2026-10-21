<script setup lang="ts">
import { ref, onUnmounted } from "vue";
import { ElMessageBox } from "element-plus";
import QualityPage from "./QualityPage.vue";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
const props = defineProps<{ initialId?: string; initialEntityId?: string }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const tab = ref("stock"),
  search = ref(""),
  loading = ref(false),
  products = ref<{ id: string; values: { name: string; code: string } }[]>([]),
  selected = ref(""),
  selectedName = ref(""),
  reason = ref("");
const settings = ref<{
  version: number;
  quality_stopped: boolean;
  values: { enabled: boolean; sales_enabled: boolean };
} | null>(null);
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: !!reason.value, busy: busy.value }),
    "quality",
  ),
);
async function query() {
  loading.value = true;
  try {
    products.value = (
      await request<{ items: typeof products.value }>(
        `/api/v1/catalog/master/products?search=${encodeURIComponent(search.value)}`,
      )
    ).items;
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
}
async function choose(p: (typeof products.value)[number]) {
  if (reason.value) {
    try {
      await ElMessageBox.confirm(
        "切换商品将放弃当前处理原因，是否继续？",
        "切换商品",
      );
    } catch {
      return;
    }
  }
  try {
    settings.value = await request(
      `/api/v1/catalog/master/products/${p.id}/entity-settings?entity_id=${activeEntity.value}`,
    );
    selected.value = p.id;
    selectedName.value = p.values.name;
    reason.value = "";
  } catch (e) {
    emit("failure", e);
  }
}
async function save() {
  if (!settings.value) return;
  await execute(
    `/api/v1/quality-sale-stops/${selected.value}`,
    {
      entity_id: activeEntity.value,
      expected_version: settings.value.version,
      stopped: !settings.value.quality_stopped,
      reason: reason.value,
    },
    async () => {
      reason.value = "";
      settings.value = await request(
        `/api/v1/catalog/master/products/${selected.value}/entity-settings?entity_id=${activeEntity.value}`,
      );
    },
  );
}
</script>
<template>
  <div>
    <nav class="master-tabs" aria-label="质量处置类型">
      <button :class="{ active: tab === 'stock' }" @click="tab = 'stock'">
        库存质量处置</button
      ><button
        :class="{ active: tab === 'stop' }"
        @click="
          tab = 'stop';
          query();
        "
      >
        商品停售 / 解除停售
      </button>
    </nav>
    <QualityPage
      v-show="tab === 'stock'"
      :initial-id="props.initialId"
      :initial-entity-id="props.initialEntityId"
      @failure="emit('failure', $event)"
    />
    <section
      v-show="tab === 'stop'"
      class="panel"
      aria-label="商品停售管理"
      v-loading="loading"
    >
      <h2>商品停售 / 解除停售</h2>
      <p class="form-note">
        作用于当前经营主体；停售后新的销售业务会按既有商品准入检查拦截。解除停售仍需满足资质、库存质量等其他条件。
      </p>
      <form class="business-toolbar" @submit.prevent="query">
        <label>查找商品<input v-model="search" maxlength="100" /></label
        ><el-button native-type="submit">查询</el-button>
      </form>
      <div class="sale-stop-layout">
        <div class="sale-stop-list">
          <button
            v-for="p in products"
            :key="p.id"
            :class="{ active: selected === p.id }"
            @click="choose(p)"
          >
            {{ p.values.name }} <small>{{ p.values.code }}</small>
          </button>
        </div>
        <form v-if="settings" class="business-form" @submit.prevent="save">
          <h3>{{ selectedName }}</h3>
          <p>
            销售状态：<strong>{{
              settings.quality_stopped ? "质量已停售" : "未设置质量停售"
            }}</strong>
            · v{{ settings.version }}
          </p>
          <p v-if="!settings.values.enabled">
            该商品在当前主体同时处于停用状态。
          </p>
          <label
            >处理原因<input v-model="reason" required maxlength="200" /></label
          ><el-button
            v-if="can('quality.review')"
            native-type="submit"
            :type="settings.quality_stopped ? 'primary' : 'danger'"
            :loading="busy"
            >{{ settings.quality_stopped ? "解除停售" : "确认停售" }}</el-button
          >
          <p v-else class="form-note">
            当前账号可查看，由具有质量审核权限的人员执行。
          </p>
        </form>
        <p v-else class="form-note">选择商品查看当前主体的销售状态。</p>
      </div>
    </section>
  </div>
</template>
<style scoped>
.sale-stop-layout {
  display: grid;
  grid-template-columns: minmax(180px, 320px) minmax(0, 1fr);
  gap: 24px;
}
.sale-stop-list {
  max-height: 500px;
  overflow: auto;
}
.sale-stop-list button {
  display: block;
  width: 100%;
  text-align: left;
  border: 1px solid var(--ui-border);
  background: var(--ui-surface, #fff);
  padding: 12px;
  cursor: pointer;
  color: var(--ui-text);
}
.sale-stop-list button.active {
  background: var(--ui-soft);
  color: var(--ui-accent);
}
.sale-stop-list small {
  display: block;
  color: var(--ui-muted);
  margin-top: 5px;
}
@media (max-width: 700px) {
  .sale-stop-layout {
    grid-template-columns: 1fr;
  }
}
</style>
