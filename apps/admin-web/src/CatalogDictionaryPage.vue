<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import { localTime } from "./sales";
import { checkDictionary, type CatalogDictionary } from "./catalogDictionaries";
import { validationOptions } from "./productFieldRules";
const props = defineProps<{ initialKey?: string; embedded?: boolean }>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
  saved: [];
}>();
const dictionaries = ref<CatalogDictionary[]>([]),
  selected = ref<CatalogDictionary | null>(null),
  loading = ref(false);
const form = ref({
    mode: "SUGGEST" as "SUGGEST" | "STRICT",
    items: [] as CatalogDictionary["items"],
  }),
  baseline = ref(""),
  reason = ref("");
const signature = () => JSON.stringify(form.value);
const dirty = computed(
  () => !!selected.value && (signature() !== baseline.value || !!reason.value),
);
const { busy, execute } = useCommand((e) => emit("failure", e));
watch(
  () => dirty.value || busy.value || loading.value,
  (value) => emit("editing", value),
  { immediate: true },
);
const checks = computed(() =>
  (selected.value?.bindings || []).map((binding) => ({
    ...binding,
    ...checkDictionary(form.value, binding),
  })),
);
const purchaseDictionary = computed(() =>
  [
    "purchase_approval",
    "inspection_qualified",
    "inspection_mixed",
    "inspection_rejected",
  ].includes(selected.value?.key || ""),
);
const blocked = computed(
  () =>
    checks.value.some((c) => c.blocked) ||
    (purchaseDictionary.value && !form.value.items.some((i) => i.active)),
);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    "dictionaries",
  ),
);
const fixed = computed(() =>
  [
    "abc_class",
    "recommendation_level",
    "drug_business_scope",
    "device_business_scope",
    "purchase_approval",
    "inspection_qualified",
    "inspection_mixed",
    "inspection_rejected",
  ].includes(selected.value?.key || ""),
);
let epoch = 0;
onUnmounted(() => {
  epoch++;
});
function reset() {
  if (!selected.value) return;
  form.value = {
    mode: selected.value.mode,
    items: selected.value.items.map((i) => ({ ...i })),
  };
  baseline.value = signature();
  reason.value = "";
}
async function load(key?: string) {
  const current = ++epoch;
  loading.value = true;
  try {
    const list = await request<{ items: CatalogDictionary[] }>(
      "/api/v1/catalog/dictionaries",
    );
    const detail = await request<CatalogDictionary>(
      `/api/v1/catalog/dictionaries/${key || list.items[0].key}`,
    );
    if (current !== epoch) return;
    dictionaries.value = list.items;
    selected.value = detail;
    reset();
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function add() {
  form.value.items.push({
    value: "",
    active: true,
    order: Math.min(
      9999,
      Math.max(0, ...form.value.items.map((i) => i.order)) + 10,
    ),
  });
}
async function save() {
  if (!selected.value || blocked.value) return;
  const key = selected.value.key;
  await execute(
    `/api/v1/catalog/dictionaries/${key}`,
    {
      expected_version: selected.value.version,
      ...form.value,
      reason: reason.value,
    },
    async () => {
      await load(key);
      emit("saved");
    },
  );
}
onMounted(() => load(props.initialKey));
</script>
<template>
  <section
    class="panel dictionary-page"
    aria-label="商品字典"
    v-loading="loading"
  >
    <div class="business-toolbar">
      <h2>{{ embedded ? selected?.name || "维护字典" : "商品字典" }}</h2>
      <span class="form-note">统一选项 · 按字段绑定 · 保留历史值</span>
    </div>
    <p class="form-note">
      字典统一维护商品、往来单位和采购办理中的常用选项。药品、器械经营范围类别分开维护，请依据企业已核对的范围设置。保存后，新打开的资料或办理窗口使用最新字典。
    </p>
    <div class="dictionary-layout" :class="{ embedded }">
      <nav v-if="!embedded" aria-label="字典字段">
        <button
          v-for="d in dictionaries"
          :key="d.key"
          type="button"
          :class="{ active: selected?.key === d.key }"
          :disabled="busy || loading || (dirty && selected?.key !== d.key)"
          @click="!dirty && load(d.key)"
        >
          {{ d.name
          }}<small>{{ d.items.filter((i) => i.active).length }} 个启用值</small>
        </button>
      </nav>
      <form
        v-if="selected"
        class="business-form dictionary-content"
        @submit.prevent="save"
      >
        <h3 v-if="!embedded">{{ selected.name }}</h3>
        <p class="form-note">
          字典值保存后不能改写或删除；可停用后新增。停用不修改历史资料，原值可保留，新录入不能再选。
        </p>
        <fieldset :disabled="busy || loading || !can('catalog.configure')">
          <label
            >选值规则<select
              v-model="form.mode"
              :disabled="fixed"
              aria-label="选值规则"
            >
              <option value="SUGGEST">建议选用，可输入其他值</option>
              <option value="STRICT">只能选用启用的字典值</option>
            </select></label
          >
          <section
            v-if="purchaseDictionary"
            class="dictionary-usage"
            aria-label="采购结论规则"
          >
            <strong>同类结论用语 · 排序靠前的启用项为默认值</strong>
            <p class="form-note">
              审核意见仅用于通过审核时的意见表述。验收结论按实际合格、拒收数量分为合格、部分拒收、全部拒收三类，请在对应类别维护同义用语。字典不能改变实际数量、审核结果或操作权限。
            </p>
            <p v-if="blocked" role="alert">
              至少保留一个启用值，保证办理时有可选结论。
            </p>
          </section>
          <section v-else class="dictionary-usage" aria-label="字典适用规则">
            <strong>适用字段与填写限制</strong>
            <p class="form-note">
              同时核对已发布的多品类主数据规则和原有资料规则。字典保存与配置发布都会检查；不符合限制的选项不会出现在对应字段中。
            </p>
            <div
              v-for="c in checks"
              :key="`${c.category}:${c.field}`"
              class="binding"
              :class="{ conflict: c.blocked }"
            >
              <b
                >{{ c.category_name }} · {{ c.label
                }}<small
                  >（{{
                    c.source === "MDM" ? "主数据规则" : "原有资料规则"
                  }}）</small
                ></b
              >
              <span
                >{{
                  c.required_when.length
                    ? "含条件必填"
                    : c.required
                      ? "必填"
                      : "选填"
                }}
                · {{ c.min_length }}–{{ c.max_length }} 字符 ·
                {{ validationOptions[c.validator] }}</span
              >
              <span>符合规则 {{ c.eligible }} / {{ c.active }} 个启用值</span>
              <span v-if="c.blocked" role="alert"
                >无法保存：必填字段没有符合规则的可选值，请启用或补充可用值。</span
              >
              <span v-else-if="!c.eligible" class="form-note">{{
                form.mode === "SUGGEST"
                  ? "可按字段规则手工填写。"
                  : "没有可选值，选填字段可留空。"
              }}</span>
              <span v-if="c.incompatible.length" class="form-note"
                >不符合规则：{{ c.incompatible.slice(0, 5).join("、")
                }}{{
                  c.incompatible.length > 5 ? " 等" : ""
                }}。这些值仍保留在字典中。</span
              >
            </div>
          </section>
          <div class="dictionary-table">
            <table>
              <thead>
                <tr>
                  <th>字典值</th>
                  <th>排序（小值靠前）</th>
                  <th>启用</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in form.items" :key="index">
                  <td>
                    <input
                      v-model="item.value"
                      :aria-label="`字典值 ${index + 1}`"
                      :readonly="index < selected.items.length"
                      required
                      :maxlength="selected.capacity"
                    />
                  </td>
                  <td>
                    <input
                      v-model.number="item.order"
                      :aria-label="`排序 ${index + 1}`"
                      type="number"
                      min="0"
                      max="9999"
                      step="1"
                      required
                    />
                  </td>
                  <td>
                    <input
                      v-model="item.active"
                      type="checkbox"
                      :aria-label="`启用 ${index + 1}`"
                    />
                  </td>
                  <td>
                    <el-button
                      v-if="index >= selected.items.length"
                      :disabled="busy"
                      @click="form.items.splice(index, 1)"
                      >移除未保存项</el-button
                    ><span v-else class="form-note">已保留</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="!form.items.length" class="form-note">尚未设置字典值。</p>
          <el-button
            v-if="can('catalog.configure') && selected.key !== 'abc_class'"
            :disabled="busy || form.items.length >= 200"
            @click="add"
            >新增字典值</el-button
          >
          <label v-if="can('catalog.configure')" class="dictionary-reason"
            >变更说明<input v-model="reason" required maxlength="200"
          /></label>
        </fieldset>
        <div class="form-actions" v-if="can('catalog.configure')">
          <span class="form-note">{{
            dirty
              ? "请先保存或撤销，再切换字典。"
              : "当前版本 " + selected.version
          }}</span
          ><el-button :disabled="busy || !dirty" @click="reset"
            >撤销字典修改</el-button
          ><el-button
            native-type="submit"
            type="primary"
            :disabled="!dirty || loading || blocked"
            :loading="busy"
            >保存字典</el-button
          >
        </div>
        <details v-if="selected.history?.length">
          <summary>最近字典变更</summary>
          <p v-for="(h, index) in selected.history" :key="index">
            {{ localTime(h.created_at) }} · {{ h.detail.reason }} · 版本
            {{ h.detail.version }}
          </p>
        </details>
      </form>
    </div>
  </section>
</template>
<style scoped>
h2,
h3 {
  margin: 0;
}
h2 {
  font-size: 19px;
}
h3 {
  font-size: 16px;
}
.dictionary-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 24px;
  margin-top: 20px;
}
.dictionary-layout.embedded {
  grid-template-columns: minmax(0, 1fr);
}
.dictionary-usage {
  margin: 16px 0;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
}
.binding {
  display: grid;
  gap: 5px;
  padding: 10px 0;
}
.binding + .binding {
  border-top: 1px solid var(--line);
}
.binding.conflict {
  color: var(--el-color-danger-dark-2);
}
nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
nav button {
  border: 1px solid var(--line);
  background: var(--surface, #fff);
  color: inherit;
  padding: 12px;
  text-align: left;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}
nav button.active {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
nav small {
  display: block;
  margin-top: 5px;
  color: var(--muted, #64748b);
}
fieldset {
  padding: 0;
  border: 0;
  min-width: 0;
}
.dictionary-content {
  max-width: none;
  min-width: 0;
}
.dictionary-table {
  overflow-x: auto;
  margin: 16px 0;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  border-bottom: 1px solid var(--line);
  text-align: left;
  padding: 8px;
}
td:first-child {
  min-width: 180px;
}
td:nth-child(2) {
  width: 160px;
}
td:nth-child(3) {
  width: 60px;
}
td:last-child {
  width: 150px;
}
input[type="checkbox"] {
  width: 18px;
  height: 18px;
}
.dictionary-reason {
  margin-top: 16px;
}
.form-actions {
  margin: 16px 0;
}
summary {
  cursor: pointer;
}
@media (max-width: 800px) {
  .dictionary-layout {
    grid-template-columns: 1fr;
  }
  nav {
    flex-direction: row;
    overflow-x: auto;
  }
  nav button {
    flex-shrink: 0;
  }
}
</style>
