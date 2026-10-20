<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { useCommand } from "./business";
import ProductFields from "./ProductFields.vue";
import CatalogDictionaryPage from "./CatalogDictionaryPage.vue";
import ProductCodeRules from "./ProductCodeRules.vue";
import { checkDictionary } from "./catalogDictionaries";
import { registerCatalogChanges } from "./catalogChanges";
import {
  validationOptions,
  writableRules,
  fieldCards,
  fieldReviews,
  type ProductFieldPolicy,
  type ProductFieldRule,
} from "./productFieldRules";
const props = withDefaults(defineProps<{ active?: boolean }>(), {
  active: true,
});
const emit = defineEmits<{ failure: [error: unknown] }>();
const category = ref<"DRUG" | "DEVICE">("DEVICE");
const policy = ref<ProductFieldPolicy | null>(null);
const original = ref("");
const reason = ref("");
const loading = ref(false);
const search = ref("");
const card = ref("");
const editing = ref<ProductFieldRule | null>(null);
const preview = ref(false);
const dictionaryKey = ref("");
const dictionaryEditing = ref(false);
const codeRulesOpen = ref(false),
  codeRulesEditing = ref(false);
const example = ref<Record<string, string>>({
  device_class: "I",
  drug_sale_class: "OTC",
});
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () =>
    !!policy.value &&
    JSON.stringify(writableRules(policy.value.fields)) !== original.value,
);
const pending = computed(
  () => dirty.value || !!reason.value || !!editing.value,
);
onUnmounted(
  registerCatalogChanges(() => ({ dirty: pending.value, busy: busy.value })),
);
const conflicts = computed(() =>
  (policy.value?.fields || []).filter(
    (f) => f.dictionary && checkDictionary(f.dictionary, f).blocked,
  ),
);
function check(field: ProductFieldRule) {
  return field.dictionary ? checkDictionary(field.dictionary, field) : null;
}
async function refreshDictionaries() {
  const current = epoch,
    currentCategory = category.value;
  try {
    const result = await request<ProductFieldPolicy>(
      `/api/v1/catalog/field-rules/${currentCategory}`,
    );
    if (current !== epoch || !policy.value) return;
    policy.value.unit_dictionary = result.unit_dictionary;
    for (const field of policy.value.fields) {
      field.dictionary = result.fields.find(
        (f) => f.key === field.key,
      )?.dictionary;
    }
    if (editing.value)
      editing.value.dictionary = result.fields.find(
        (f) => f.key === editing.value?.key,
      )?.dictionary;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  }
}
watch(
  () => props.active,
  (active) => {
    if (active && policy.value) void refreshDictionaries();
  },
);
async function returnToFields() {
  if (dictionaryEditing.value || loading.value) return;
  loading.value = true;
  await refreshDictionaries();
  dictionaryKey.value = "";
  loading.value = false;
}
const ordered = computed(() =>
  [...(policy.value?.fields || [])].sort(
    (a, b) => a.order - b.order || a.key.localeCompare(b.key),
  ),
);
const selector = computed(() =>
  policy.value?.fields.find((f) => f.kind === "select"),
);
const filtered = computed(() =>
  ordered.value.filter(
    (f) =>
      (!card.value || f.card === card.value) &&
      `${f.label} ${f.display_name} ${f.field_code}`
        .toLocaleLowerCase()
        .includes(search.value.trim().toLocaleLowerCase()),
  ),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load(next = category.value) {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<ProductFieldPolicy>(
      `/api/v1/catalog/field-rules/${next}`,
    );
    if (current !== epoch) return;
    category.value = next;
    policy.value = result;
    original.value = JSON.stringify(writableRules(result.fields));
    reason.value = "";
    editing.value = null;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function conditions(field: ProductFieldRule) {
  if (!field.required_when.length) return "无";
  return (
    field.required_when
      .map((c) => selector.value?.options[c.equals] || c.equals)
      .join(" 或 ") + "时必填"
  );
}
function edit(field: ProductFieldRule) {
  editing.value = JSON.parse(JSON.stringify(field)) as ProductFieldRule;
}
function condition(value: string, checked: boolean) {
  if (!editing.value || !selector.value) return;
  editing.value.required_when = editing.value.required_when.filter(
    (c) => c.equals !== value,
  );
  if (checked)
    editing.value.required_when.push({
      field: selector.value.key as "device_class" | "drug_sale_class",
      equals: value,
    });
}
function apply() {
  if (!editing.value || !policy.value) return;
  const i = policy.value.fields.findIndex((f) => f.key === editing.value?.key);
  policy.value.fields[i] = editing.value;
  editing.value = null;
}
async function save() {
  if (!policy.value || !dirty.value || conflicts.value.length) return;
  if (!reason.value.trim()) {
    emit("failure", new Error("请填写本次字段设置的修改说明"));
    return;
  }
  const current = category.value;
  await execute(
    `/api/v1/catalog/field-rules/${current}`,
    {
      expected_version: policy.value.version,
      fields: writableRules(policy.value.fields),
      reason: reason.value,
    },
    async () => {
      await load(current);
    },
  );
}
onMounted(() => load());
</script>
<template>
  <div v-if="codeRulesOpen">
    <el-button :disabled="codeRulesEditing" @click="codeRulesOpen = false"
      >← 返回字段设置</el-button
    >
    <ProductCodeRules
      :category="category"
      @editing="codeRulesEditing = $event"
      @failure="emit('failure', $event)"
    />
  </div>
  <div v-if="dictionaryKey" class="field-dictionary-editor">
    <div class="business-toolbar dictionary-return">
      <el-button
        :disabled="dictionaryEditing || loading"
        @click="returnToFields"
        >← 返回字段设置</el-button
      >
      <span class="form-note"
        >字段调整已在当前页面保留。字典单独保存；修改字典后请返回检查并保存字段设置。</span
      >
    </div>
    <CatalogDictionaryPage
      :key="dictionaryKey"
      :initial-key="dictionaryKey"
      embedded
      @failure="emit('failure', $event)"
      @editing="dictionaryEditing = $event"
      @saved="refreshDictionaries"
    />
  </div>
  <section
    v-show="!dictionaryKey && !codeRulesOpen"
    class="panel field-settings"
    v-loading="loading"
  >
    <div class="business-toolbar">
      <el-button
        v-for="c in ['DEVICE', 'DRUG'] as const"
        :key="c"
        :type="category === c ? 'primary' : 'default'"
        :disabled="busy || loading || pending"
        @click="load(c)"
        >{{ c === "DEVICE" ? "医疗器械字段" : "药品字段" }}</el-button
      >
      <el-tag v-if="policy" effect="plain">{{
        policy.version === "0" ? "系统默认" : `第 ${policy.version} 版`
      }}</el-tag>
      <el-button :disabled="busy || loading" @click="codeRulesOpen = true"
        >自动编码规则</el-button
      >
      <span v-if="dirty" class="unsaved">尚未保存</span>
      <el-button :disabled="!policy" @click="preview = !preview">{{
        preview ? "收起录入预览" : "预览录入效果"
      }}</el-button>
    </div>
    <p class="form-note">
      设置供本企业使用，在新建、修订和审核时校验。已审核档案及历史单据不会因修改设置而被重写。编码、商品类别、SKU
      与基本单位等身份字段保持固定。
    </p>
    <div v-if="category === 'DEVICE'" class="rule-callout">
      <strong>器械证照联动 · 系统内置</strong>
      <p>
        第一类 → 产品备案号必填；第二、三类 →
        注册证号必填。两类编号按当前分类使用同一个证照编号字段，不要求重复填写。
      </p>
      <p>
        已有“待核定”资料保留；将“器械管理类别”设为必填后，后续建档、修订和审核必须选择具体类别。
      </p>
      <a
        v-if="policy?.legal_source"
        :href="policy.legal_source"
        target="_blank"
        rel="noopener noreferrer"
        >依据：《医疗器械监督管理条例》第十三条</a
      >
    </div>
    <template v-if="policy">
      <div class="template-summary">
        <div>
          <b>{{ policy.template.name }}</b
          ><small>{{ policy.template.scope }}</small>
        </div>
        <span>{{ policy.fields.length }} 个字段 · 名称与规则按企业保存</span>
      </div>
      <div class="template-filters">
        <label
          >查找字段<input
            v-model="search"
            aria-label="查找字段"
            placeholder="名称、显示名称或字段标识"
        /></label>
        <label
          >所属卡片<select v-model="card" aria-label="所属卡片">
            <option value="">全部卡片</option>
            <option v-for="(label, key) in fieldCards" :key="key" :value="key">
              {{ label }}
            </option>
          </select></label
        >
        <span class="form-note"
          >显示 {{ filtered.length }} / {{ policy.fields.length }} 个字段</span
        >
      </div>
      <div class="dictionary-guide">
        <span
          >字典控制可选值；本页控制显示、必填、长度与联动。基本单位为固定必填字段。</span
        >
        <el-button
          link
          type="primary"
          :disabled="busy || loading"
          @click="dictionaryKey = 'base_unit'"
          >维护基本单位字典</el-button
        >
      </div>
      <el-table :data="filtered" row-key="key" class="field-rules-table">
        <el-table-column label="资料字段" min-width="190"
          ><template #default="{ row }"
            ><b :title="row.meaning">{{ row.display_name || row.label }}</b
            ><small v-if="row.display_name" class="locked"
              >原名称：{{ row.label }}</small
            ><small v-if="row.locked_required" class="locked"
              >基础必填</small
            ></template
          ></el-table-column
        >
        <el-table-column label="归属与维护" min-width="160"
          ><template #default="{ row }">
            <span
              >{{ fieldCards[row.card as keyof typeof fieldCards] }} ·
              {{ row.owner_department }}</span
            >
            <small class="locked">{{
              fieldReviews[row.review_policy as keyof typeof fieldReviews]
            }}</small>
          </template></el-table-column
        >
        <el-table-column label="显示" width="65"
          ><template #default="{ row }"
            ><input
              type="checkbox"
              v-model="row.visible"
              :aria-label="`${row.label} 显示`"
              :disabled="
                busy || row.required || row.kind === 'select'
              " /></template
        ></el-table-column>
        <el-table-column label="必填" width="65"
          ><template #default="{ row }"
            ><input
              type="checkbox"
              v-model="row.required"
              :aria-label="`${row.label} 必填`"
              :disabled="busy || row.locked_required"
              @change="row.required && (row.visible = true)" /></template
        ></el-table-column>
        <el-table-column label="排序" width="90"
          ><template #default="{ row }"
            ><input
              type="number"
              v-model.number="row.order"
              :aria-label="`${row.label} 排序`"
              min="0"
              max="999"
              step="1"
              :disabled="busy"
              class="order-input" /></template
        ></el-table-column>
        <el-table-column label="填写限制" min-width="165"
          ><template #default="{ row }">{{
            row.kind === "text"
              ? `${row.min_length}–${row.max_length} 字符 · ${validationOptions[row.validator as keyof typeof validationOptions]}`
              : "按字段类型校验"
          }}</template></el-table-column
        >
        <el-table-column label="条件必填" min-width="160"
          ><template #default="{ row }">{{
            conditions(row)
          }}</template></el-table-column
        >
        <el-table-column label="选值字典" min-width="230">
          <template #default="{ row }">
            <div
              v-if="row.dictionary"
              class="field-dictionary"
              :class="{ conflict: check(row)?.blocked }"
            >
              <span
                >{{
                  row.dictionary.mode === "STRICT"
                    ? "仅选字典值"
                    : "建议选值 / 可手填"
                }}
                · 可选 {{ check(row)?.eligible }} /
                {{ check(row)?.active }}</span
              >
              <small v-if="check(row)?.blocked"
                >必填字段无可用选项，无法保存</small
              >
              <small v-else-if="check(row)?.incompatible.length"
                >{{
                  check(row)?.incompatible.length
                }}
                个值不符合本字段限制</small
              >
              <el-button
                link
                type="primary"
                :disabled="busy || loading"
                :aria-label="`维护 ${row.dictionary.name} 字典`"
                @click="dictionaryKey = row.dictionary.key"
                >维护字典</el-button
              >
            </div>
            <span v-else class="form-note">{{
              row.kind === "select" ? "系统固定分类" : "手工填写"
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="规则" width="80"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :aria-label="`配置 ${row.label}`"
              :disabled="busy"
              @click="edit(row)"
              >配置</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <p class="form-note">
        排序数字越小越靠前。隐藏字段已有内容时仍显示供核对，内容不会丢失；条件必填生效时自动显示。基础必填不能取消，格式校验不代表证照真实有效。
      </p>
      <p v-if="conflicts.length" class="dictionary-conflict" role="alert">
        无法保存：{{
          conflicts.map((f) => f.label).join("、")
        }}为必填或条件必填，但严格字典没有符合规则的启用值。请维护字典或调整长度、格式及必填规则。
      </p>
      <div class="settings-save">
        <label
          >修改说明<input
            v-model="reason"
            aria-label="修改说明"
            maxlength="200"
            placeholder="说明本次调整的用途"
            :disabled="busy"
        /></label>
        <el-button :disabled="busy || loading" @click="load()"
          >重新加载 / 放弃修改</el-button
        >
        <el-button
          type="primary"
          :loading="busy"
          :disabled="!dirty || loading || !!conflicts.length"
          @click="save"
          >保存字段设置</el-button
        >
      </div>
      <section v-if="preview" class="preview-panel">
        <h3>录入预览</h3>
        <p class="form-note">
          试填不会保存为商品；可切换分类，查看条件必填效果。
        </p>
        <ProductFields
          v-model="example"
          :fields="policy.fields"
          prefix="预览 "
        />
      </section>
      <details v-if="policy.history.length" class="rule-history">
        <summary>最近修改记录</summary>
        <p v-for="event in policy.history" :key="event.detail.version">
          第 {{ event.detail.version }} 版 ·
          {{ new Date(event.created_at).toLocaleString() }} ·
          {{ event.detail.reason }}
        </p>
      </details>
    </template>
    <el-button v-else :disabled="loading" @click="load()"
      >重新加载字段设置</el-button
    >
  </section>
  <el-dialog
    :model-value="!!editing && active"
    :title="`配置规则 · ${editing?.label || ''}`"
    width="600px"
    class="business-dialog"
    :close-on-click-modal="false"
    @close="active && (editing = null)"
  >
    <form v-if="editing" class="business-form" @submit.prevent="apply">
      <div class="field-semantics">
        <b>{{ editing.label }}</b>
        <p>{{ editing.meaning }}</p>
        <p>
          {{ fieldCards[editing.card] }} · {{ editing.owner_department }} ·
          {{ fieldReviews[editing.review_policy] }}
        </p>
        <small>稳定标识：{{ editing.field_code }}</small>
      </div>
      <div class="form-grid">
        <label v-if="editing.alias_allowed" class="full-width"
          >企业显示名称
          <input
            v-model="editing.display_name"
            aria-label="企业显示名称"
            maxlength="40"
            :placeholder="editing.label"
          />
          <small
            >留空使用原名称。名称不改变字段含义、存储内容或审核要求。</small
          >
        </label>
        <p v-else class="form-note full-width">
          此字段名称由系统或分类联动控制。
        </p>
        <template v-if="editing.kind === 'text'">
          <label
            >最少字符<input
              v-model.number="editing.min_length"
              type="number"
              required
              min="0"
              :max="editing.max_length"
              step="1"
          /></label>
          <label
            >最多字符<input
              v-model.number="editing.max_length"
              type="number"
              required
              :min="Math.max(1, editing.min_length)"
              :max="editing.capacity"
              step="1"
          /></label>
          <label class="full-width"
            >格式验证<select v-model="editing.validator">
              <option
                v-for="(name, value) in validationOptions"
                :key="value"
                :value="value"
              >
                {{ name }}
              </option>
            </select></label
          >
        </template>
        <fieldset
          v-if="editing.kind !== 'select'"
          class="full-width condition-box"
        >
          <legend>满足以下任一条件时必填</legend>
          <label
            v-for="(name, value) in selector?.options"
            v-show="value !== 'UNKNOWN'"
            :key="value"
            class="condition-choice"
          >
            <input
              type="checkbox"
              :checked="editing.required_when.some((c) => c.equals === value)"
              @change="
                condition(
                  String(value),
                  ($event.target as HTMLInputElement).checked,
                )
              "
            />{{ name }}
          </label>
          <p class="form-note">
            条件按“或”组合；始终必填优先。分类字段自身不设置联动。
          </p>
        </fieldset>
        <p v-else class="form-note full-width">
          选项由系统维护，可在列表设置是否必填及顺序。选择“待核定”不满足必填要求。
        </p>
      </div>
      <div
        v-if="editing.dictionary"
        class="dictionary-guide"
        :class="{ conflict: check(editing)?.blocked }"
      >
        <b
          >{{ editing.dictionary.name }}字典 ·
          {{
            editing.dictionary.mode === "STRICT"
              ? "仅选字典值"
              : "建议选值 / 可手填"
          }}</b
        >
        <span
          >当前符合规则 {{ check(editing)?.eligible }} /
          {{ check(editing)?.active }} 个启用值。</span
        >
        <span v-if="check(editing)?.incompatible.length"
          >不符合规则：{{ check(editing)?.incompatible.slice(0, 5).join("、")
          }}{{
            (check(editing)?.incompatible.length || 0) > 5 ? " 等" : ""
          }}</span
        >
        <span v-if="check(editing)?.blocked" role="alert"
          >应用后请维护字典或调整规则，否则无法保存字段设置。</span
        >
      </div>
      <div class="form-actions">
        <el-button @click="editing = null">取消</el-button
        ><el-button native-type="submit" type="primary"
          >应用到当前设置</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
<style scoped>
.template-summary,
.template-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 20px;
  margin: 14px 0;
}
.template-summary {
  justify-content: space-between;
  padding: 14px 16px;
  border: 1px solid var(--line);
  background: var(--el-color-primary-light-9);
  border-radius: 8px;
}
.template-summary small {
  display: block;
  margin-top: 6px;
}
.template-filters label {
  display: flex;
  align-items: center;
  gap: 8px;
}
.template-filters input,
.template-filters select {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface, white);
}
.field-semantics {
  padding: 12px;
  margin-bottom: 16px;
  background: var(--el-color-primary-light-9);
  border-radius: 6px;
  overflow-wrap: anywhere;
}
.field-semantics p {
  margin: 8px 0;
}
.dictionary-return {
  padding: 12px 0;
}
.dictionary-guide {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 12px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--line);
  border-radius: 6px;
  margin: 14px 0;
}
.field-dictionary {
  display: grid;
  justify-items: start;
  gap: 4px;
}
.field-dictionary small {
  font-size: 12px;
}
.conflict,
.dictionary-conflict {
  color: var(--el-color-danger-dark-2);
}
.dictionary-conflict {
  background: var(--el-color-danger-light-9);
  border-radius: 6px;
  padding: 12px;
}
.rule-callout {
  background: #f2f7f3;
  border-left: 3px solid #52896a;
  padding: 16px 20px;
  margin: 18px 0;
  border-radius: 8px;
}
.rule-callout p {
  margin: 8px 0;
  line-height: 1.6;
}
.rule-callout a {
  color: #267051;
}
.locked {
  display: block;
  color: #76817a;
  font-weight: 400;
}
.order-input {
  width: 66px;
  padding: 7px;
  border: 1px solid #d5dfd8;
  border-radius: 6px;
}
.settings-save {
  display: flex;
  align-items: end;
  gap: 12px;
  margin: 22px 0;
  flex-wrap: wrap;
}
.settings-save label {
  flex: 1;
  display: grid;
  gap: 8px;
  min-width: 220px;
}
.settings-save input {
  padding: 10px 12px;
  border: 1px solid #d5dfd8;
  border-radius: 6px;
}
.preview-panel {
  background: #f8faf8;
  padding: 24px;
  border: 1px solid #dce6df;
  border-radius: 12px;
  margin-top: 24px;
}
.condition-box {
  border: 1px solid #dce6df;
  border-radius: 8px;
  padding: 16px;
}
.condition-choice {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 12px 20px 8px 0;
}
.condition-choice input {
  width: auto;
}
.unsaved {
  color: #986314;
}
.rule-history {
  margin-top: 24px;
  color: #67766c;
}
</style>
