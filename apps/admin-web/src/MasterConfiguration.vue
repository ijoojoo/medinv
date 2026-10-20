<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, provide } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges, CHANGE_SCOPE } from "./catalogChanges";
provide(CHANGE_SCOPE, "productFields");
import MasterFields from "./MasterFields.vue";
import ProductCodeRules from "./ProductCodeRules.vue";
import { checkDictionary } from "./catalogDictionaries";
import {
  nodePath,
  type MasterSchema,
  type SchemaResponse,
  type MasterValues,
  type MasterField,
} from "./productMaster";
defineProps<{ active?: boolean }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const data = ref<SchemaResponse | null>(null),
  draft = ref<MasterSchema | null>(null),
  baseline = ref(""),
  draftId = ref<string | null>(null),
  version = ref(0),
  base = ref(0),
  reason = ref("调整商品主数据配置");
const section = ref("rules"),
  profile = ref("drug"),
  group = ref("basic"),
  previewOpen = ref(false),
  codeOpen = ref(false),
  previewValues = ref<MasterValues>({});
const impact = ref<{
  errors: string[];
  impacts: Record<string, unknown>[];
  changed_profiles?: string[];
  products_requiring_revalidation?: number;
  preview_token: string;
  note?: string;
} | null>(null);
const custom = ref({ key: "custom_", label: "", type: "text", private: false }),
  nodeName = ref(""),
  parent = ref(""),
  nodeProfile = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () => !!draft.value && JSON.stringify(draft.value) !== baseline.value,
);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    "productFields",
    async () => {
      await save();
      return !dirty.value && !busy.value;
    },
  ),
);
const visibleFields = computed(
  () => draft.value?.fields.filter((f) => f.tab === group.value) || [],
);
const dictionaryChecks = computed(() =>
  Object.fromEntries(
    visibleFields.value.flatMap((f) => {
      const dictionary = data.value?.dictionaries?.[f.key];
      const rule = draft.value!.rules[profile.value][f.key];
      if (!dictionary || !rule.applicable) return [];
      return [
        [
          f.key,
          {
            name: dictionary.name,
            ...checkDictionary(dictionary, {
              ...rule,
              required:
                rule.required ||
                (f.required && f.profiles.includes(profile.value)),
              required_when: rule.required_when ? [rule.required_when] : [],
              min_length: rule.min_length || 0,
              max_length: Math.min(rule.max_length || 500, dictionary.capacity),
              validator: rule.validator || "NONE",
              reject_placeholders: true,
            }),
          },
        ],
      ];
    }),
  ),
);
const previewData = computed(() =>
  data.value && draft.value ? { ...data.value, payload: draft.value } : null,
);
onMounted(load);
async function load() {
  try {
    data.value = await request<SchemaResponse>("/api/v1/catalog/master/schema");
    draft.value = JSON.parse(JSON.stringify(data.value.payload));
    for (const f of draft.value!.fields)
      for (const p of f.profiles) {
        if (f.required) draft.value!.rules[p][f.key].required = true;
      }
    base.value = data.value.revision;
    baseline.value = JSON.stringify(draft.value);
    draftId.value = null;
    version.value = 0;
    impact.value = null;
  } catch (e) {
    emit("failure", e);
  }
}
async function save() {
  if (!draft.value) return;
  const snapshot = JSON.stringify(draft.value);
  await execute<{ id: string; version: number; preview: typeof impact.value }>(
    "/api/v1/catalog/master/schema/draft",
    {
      draft_id: draftId.value,
      expected_version: version.value,
      base_revision: base.value,
      payload: JSON.parse(snapshot),
      reason: reason.value,
    },
    (r) => {
      draftId.value = r.id;
      version.value = r.version;
      impact.value = r.preview;
      baseline.value = snapshot;
    },
  );
}
async function inspect(id: string, restore = false) {
  try {
    const r = await request<{
      id: string;
      payload: MasterSchema;
      version: number;
      base_revision: number;
      state: string;
      preview: typeof impact.value;
    }>(`/api/v1/catalog/master/schema/${id}`);
    draft.value = r.payload;
    const copyToCurrent =
      restore ||
      r.state === "PUBLISHED" ||
      r.base_revision !== data.value!.revision;
    if (copyToCurrent && data.value) {
      for (const n of data.value.payload.nodes)
        if (!draft.value.nodes.some((x) => x.node_id === n.node_id))
          draft.value.nodes.push({
            ...n,
            enabled: false,
            is_assignable: false,
          });
      for (const field of data.value.payload.fields)
        if (!draft.value.fields.some((f) => f.key === field.key)) {
          const retained = JSON.parse(JSON.stringify(field)) as MasterField;
          if (retained.lifecycle !== "BUILTIN") retained.lifecycle = "INACTIVE";
          draft.value.fields.push(retained);
          draft.value.columns.push({
            key: retained.key,
            label: retained.default_label,
            value_type: retained.value_type,
            projection: retained.key,
          });
          const slot = data.value.payload.slots.find(
            (s) => s.field_key === retained.key,
          )!;
          draft.value.slots.push({
            ...slot,
            position:
              Math.max(
                -1,
                ...draft.value.slots
                  .filter((s) => s.tab === slot.tab)
                  .map((s) => s.position),
              ) + 1,
          });
          for (const p of Object.keys(draft.value.rules))
            draft.value.rules[p][retained.key] =
              retained.lifecycle === "BUILTIN"
                ? { ...data.value.payload.rules[p][retained.key] }
                : {
                    ...data.value.payload.rules[p][retained.key],
                    applicable: false,
                    required: false,
                    required_when: null,
                    read_only: true,
                  };
        }
      for (const f of draft.value.fields)
        for (const p of f.profiles)
          if (f.required) draft.value.rules[p][f.key].required = true;
    }
    draftId.value = copyToCurrent ? null : r.id;
    version.value = draftId.value ? r.version : 0;
    base.value = draftId.value ? r.base_revision : data.value!.revision;
    baseline.value = draftId.value
      ? JSON.stringify(draft.value)
      : JSON.stringify(data.value!.payload);
    impact.value = draftId.value ? r.preview : null;
  } catch (e) {
    emit("failure", e);
  }
}
async function publish() {
  if (!draftId.value || !impact.value) return;
  await execute(
    `/api/v1/catalog/master/schema/${draftId.value}/publish`,
    {
      expected_version: version.value,
      preview_token: impact.value.preview_token,
      reason: reason.value,
    },
    load,
  );
}
async function customField() {
  try {
    const field = await request<MasterField>(
      `/api/v1/catalog/master/custom-field?${new URLSearchParams({ key: custom.value.key, label: custom.value.label, kind: custom.value.type, private: String(custom.value.private) })}`,
    );
    if (!draft.value || draft.value.fields.some((f) => f.key === field.key))
      throw new Error("字段键已存在，不能回收身份");
    draft.value.fields.push(field);
    draft.value.columns.push({
      key: field.key,
      label: field.default_label,
      value_type: field.value_type,
      projection: field.key,
    });
    draft.value.slots.push({
      slot_id: "C_" + field.key,
      field_key: field.key,
      tab: "custom",
      position: draft.value.slots.filter((s) => s.tab === "custom").length,
      height: 94,
      span: 1,
    });
    Object.values(draft.value.rules).forEach(
      (r) =>
        (r[field.key] = {
          applicable: true,
          label: "",
          required: false,
          read_only: false,
          required_when: null,
        }),
    );
    custom.value = { key: "custom_", label: "", type: "text", private: false };
    group.value = "custom";
  } catch (e) {
    emit("failure", e);
  }
}
function addNode() {
  if (!draft.value || !nodeName.value || !parent.value) return;
  const id = crypto.randomUUID();
  draft.value.nodes.push({
    node_id: id,
    node_code: "tenant_" + id.replaceAll("-", ""),
    name: nodeName.value,
    parent_id: parent.value,
    sort_order: draft.value.nodes.length,
    enabled: true,
    is_assignable: !!nodeProfile.value,
    field_profile_id: nodeProfile.value || null,
  });
  nodeName.value = "";
}
function moveSlot(key: string, delta: number) {
  if (!draft.value) return;
  const slots = draft.value.slots
    .filter((s) => s.tab === group.value)
    .sort((a, b) => a.position - b.position);
  const index = slots.findIndex((s) => s.field_key === key),
    other = slots[index + delta];
  if (other && group.value !== "basic") {
    [slots[index].position, other.position] = [
      other.position,
      slots[index].position,
    ];
  }
}
function condition(key: string, selector: string) {
  draft.value!.rules[profile.value][key].required_when = selector
    ? { field: selector, equals: "" }
    : null;
}
</script>
<template>
  <section class="master-config" v-if="draft && data">
    <div class="master-toolbar">
      <div>
        <h2>商品主数据设置</h2>
        <small
          >当前已发布 v{{ data.revision }} ·
          {{ draftId ? "配置草稿 v" + version : "新配置草稿" }} ·
          {{ dirty ? "有未保存修改" : "已保存" }}
        </small>
      </div>
      <div>
        <el-button @click="codeOpen = true">商品编码规则</el-button
        ><el-button @click="previewOpen = true">跨品类布局预览</el-button
        ><el-button @click="load" :disabled="dirty">重新加载</el-button>
      </div>
    </div>
    <div class="master-tabs">
      <button
        v-for="s in [
          ['rules', '品类字段规则'],
          ['tree', '品类树'],
          ['catalog', '字段目录'],
          ['layout', '公共布局'],
          ['versions', '版本与发布'],
        ]"
        :key="s[0]"
        :class="{ active: section === s[0] }"
        @click="section = s[0]"
      >
        {{ s[1] }}
      </button>
    </div>
    <div
      v-if="['rules', 'catalog', 'layout'].includes(section)"
      class="master-search"
    >
      <label v-if="section === 'rules'"
        >品类
        <select v-model="profile">
          <option v-for="(r, p) in draft.rules" :key="p" :value="p">
            {{ draft.nodes.find((n) => n.field_profile_id === p)?.name }}
          </option>
        </select></label
      ><label
        >分组
        <select v-model="group">
          <option v-for="t in draft.tabs" :key="t.id" :value="t.id">
            {{ t.label }}
          </option>
        </select></label
      ><span>字段含义和存储绑定固定，品类规则不控制布局。</span>
    </div>
    <div v-if="section === 'rules'" class="master-table-scroll">
      <table class="master-list-table">
        <thead>
          <tr>
            <th>逻辑字段</th>
            <th>品类别名</th>
            <th>适用</th>
            <th>必填</th>
            <th>只读</th>
            <th>条件必填</th>
            <th>格式与长度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in visibleFields" :key="f.key">
            <td :title="f.semantic_definition">
              {{ f.default_label }}<small>{{ f.key }}</small>
              <small v-if="dictionaryChecks[f.key]">
                字典：{{ dictionaryChecks[f.key].name }} · 可选
                {{ dictionaryChecks[f.key].eligible }} /
                {{ dictionaryChecks[f.key].active }}
              </small>
              <small
                v-if="dictionaryChecks[f.key]?.blocked"
                role="alert"
                class="dictionary-conflict"
              >
                必填字段无可用字典值，发布前请补充字典或调整规则。
              </small>
            </td>
            <td>
              <input
                v-model="draft.rules[profile][f.key].label"
                :placeholder="f.default_label"
                :disabled="!f.alias_allowed || !can('catalog.configure')"
                maxlength="40"
              />
            </td>
            <td>
              <input
                type="checkbox"
                v-model="draft.rules[profile][f.key].applicable"
                :disabled="
                  !f.profiles.includes(profile) ||
                  f.required ||
                  !can('catalog.configure')
                "
              />
            </td>
            <td>
              <input
                type="checkbox"
                v-model="draft.rules[profile][f.key].required"
                :disabled="
                  !draft.rules[profile][f.key].applicable ||
                  f.required ||
                  !can('catalog.configure')
                "
              />
            </td>
            <td>
              <input
                type="checkbox"
                v-model="draft.rules[profile][f.key].read_only"
                :disabled="!can('catalog.configure')"
              />
            </td>
            <td>
              <select
                :value="draft.rules[profile][f.key].required_when?.field || ''"
                @change="
                  condition(f.key, ($event.target as HTMLSelectElement).value)
                "
                :disabled="!can('catalog.configure')"
              >
                <option value="">无条件</option>
                <option value="regulatory_subtype">监管子类型</option>
                <option v-if="profile === 'device'" value="device_class">
                  器械类别
                </option></select
              ><select
                v-if="draft.rules[profile][f.key].required_when"
                v-model="draft.rules[profile][f.key].required_when!.equals"
              >
                <option value="">选择触发值</option>
                <option
                  v-for="[v, l] in draft.rules[profile][f.key].required_when!
                    .field === 'device_class'
                    ? [
                        ['I', '一类'],
                        ['II', '二类'],
                        ['III', '三类'],
                      ]
                    : data.subtypes[profile]"
                  :key="v"
                  :value="v"
                >
                  {{ l }}
                </option>
              </select>
            </td>
            <td>
              <template v-if="f.value_type === 'text'">
                <select
                  v-model="draft.rules[profile][f.key].validator"
                  aria-label="格式规则"
                >
                  <option :value="undefined">默认格式</option>
                  <option value="NONE">不限格式</option>
                  <option value="NO_WHITESPACE">不含空白</option>
                  <option value="ASCII_CODE">字母数字编码</option>
                  <option value="DIGITS">仅数字</option>
                </select>
                <input
                  type="number"
                  min="0"
                  max="500"
                  placeholder="最短"
                  aria-label="最小长度"
                  style="width: 66px"
                  v-model.number="draft.rules[profile][f.key].min_length"
                />
                <input
                  type="number"
                  min="0"
                  max="500"
                  placeholder="最长"
                  aria-label="最大长度"
                  style="width: 66px"
                  v-model.number="draft.rules[profile][f.key].max_length"
                /> </template
              ><span v-else>遵循字段类型</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <template v-if="section === 'tree'"
      ><p class="master-config-note">
        移动节点会改变查询汇总范围，发布前必须查看影响。现有品类的身份与监管配置不能通过改名或移动替换。
      </p>
      <div class="master-table-scroll">
        <table class="master-list-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>父级</th>
              <th>顺序</th>
              <th>启用</th>
              <th>允许建档</th>
              <th>监管配置</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in draft.nodes" :key="n.node_id">
              <td>
                <input
                  v-model="n.name"
                  :disabled="n.node_code === 'catalog_root'"
                  maxlength="120"
                /><small>{{ n.node_code }}</small>
              </td>
              <td>
                <select
                  v-model="n.parent_id"
                  :disabled="n.node_code === 'catalog_root'"
                >
                  <option :value="null">根节点</option>
                  <option
                    v-for="p in draft.nodes.filter(
                      (p) => p.node_id !== n.node_id,
                    )"
                    :key="p.node_id"
                    :value="p.node_id"
                  >
                    {{ p.name }}
                  </option>
                </select>
              </td>
              <td>
                <input
                  type="number"
                  v-model.number="n.sort_order"
                  min="0"
                  style="width: 70px"
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  v-model="n.enabled"
                  :disabled="n.node_code === 'catalog_root'"
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  v-model="n.is_assignable"
                  :disabled="
                    !n.field_profile_id || n.node_code === 'legacy_drug'
                  "
                />
              </td>
              <td>{{ n.field_profile_id || "分组" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="master-search">
        <input v-model="nodeName" placeholder="新增导航节点名称" /><select
          v-model="parent"
        >
          <option value="">选择父节点</option>
          <option v-for="n in draft.nodes" :key="n.node_id" :value="n.node_id">
            {{ n.name }}
          </option></select
        ><select v-model="nodeProfile">
          <option value="">仅分组，不允许建档</option>
          <option v-for="(r, p) in draft.rules" :key="p" :value="p">
            {{ p }}
          </option></select
        ><el-button
          :disabled="!nodeName || !parent || !can('catalog.configure')"
          @click="addNode"
          >新增节点</el-button
        >
      </div></template
    >
    <template v-if="section === 'catalog'"
      ><table class="master-list-table">
        <thead>
          <tr>
            <th>字段名称 / 稳定键</th>
            <th>语义与层级</th>
            <th>类型 / 绑定</th>
            <th>生命周期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in visibleFields" :key="f.key">
            <td>
              {{ f.default_label }}<small>{{ f.key }}</small>
            </td>
            <td style="white-space: normal; max-width: 400px">
              {{ f.semantic_definition }}<small>{{ f.data_scope }}</small>
            </td>
            <td>
              {{ f.value_type }}<small>{{ f.binding_key }}</small>
            </td>
            <td>
              <select v-if="f.lifecycle !== 'BUILTIN'" v-model="f.lifecycle">
                <option value="CUSTOM">启用</option>
                <option value="INACTIVE">停用并保留值</option></select
              ><span v-else>内置</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="master-search">
        <input v-model="custom.key" placeholder="custom_英文稳定键" /><input
          v-model="custom.label"
          placeholder="自定义属性名称"
        /><select v-model="custom.type">
          <option
            v-for="[v, l] in [
              ['text', '文本'],
              ['decimal', '小数'],
              ['integer', '正整数'],
              ['boolean', '是 / 否'],
              ['date', '日期'],
            ]"
            :key="v"
            :value="v"
          >
            {{ l }}
          </option></select
        ><label
          ><input type="checkbox" v-model="custom.private" />仅质管可查看</label
        ><el-button
          :disabled="!custom.label || !can('catalog.configure')"
          @click="customField"
          >添加自定义字段</el-button
        >
      </div></template
    >
    <template v-if="section === 'layout'"
      ><p class="master-config-note">
        固定三列 / 两列 / 单列随容器宽度响应，所有品类共用布局。B001–B015
        固定保留；其他页签调整统一顺序后发布新版本。
      </p>
      <table class="master-list-table">
        <thead>
          <tr>
            <th>槽位身份</th>
            <th>字段</th>
            <th>统一顺序</th>
            <th>高度</th>
            <th>调整</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="s in draft.slots
              .filter((s) => s.tab === group)
              .sort((a, b) => a.position - b.position)"
            :key="s.slot_id"
          >
            <td>{{ s.slot_id }}</td>
            <td>
              {{
                draft.fields.find((f) => f.key === s.field_key)?.default_label
              }}
            </td>
            <td>{{ s.position + 1 }}</td>
            <td>{{ s.height }}px</td>
            <td>
              <el-button
                :disabled="group === 'basic'"
                @click="moveSlot(s.field_key, -1)"
                >上移</el-button
              ><el-button
                :disabled="group === 'basic'"
                @click="moveSlot(s.field_key, 1)"
                >下移</el-button
              >
            </td>
          </tr>
        </tbody>
      </table></template
    >
    <template v-if="section === 'versions'"
      ><div class="master-config-note">
        <p>
          草稿保存后生成冲突与影响预览；只有该预览仍有效才允许一次性发布。历史版本复制为新草稿，不改写已发布版本或商品历史。
        </p>
        <p v-if="impact">
          影响规则品类：{{
            impact.changed_profiles?.join("、") || "无"
          }}；需重新校验商品
          {{ impact.products_requiring_revalidation || 0 }} 项。{{
            impact.note
          }}
        </p>
        <div v-for="(i, index) in impact?.impacts" :key="index">
          {{ (i.path as string[]).join(" / ") }}：受影响商品 {{ i.products }} 项
        </div>
        <p v-for="e in impact?.errors" :key="e">{{ e }}</p>
        <el-button v-if="draftId" @click="inspect(draftId)"
          >刷新影响预览</el-button
        ><el-button
          type="primary"
          :disabled="
            !draftId ||
            dirty ||
            !impact?.preview_token ||
            !can('catalog.configure')
          "
          :loading="busy"
          @click="publish"
          >发布已预览版本</el-button
        >
      </div>
      <table class="master-list-table">
        <thead>
          <tr>
            <th>配置版本</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in data.history" :key="h.id">
            <td>v{{ h.revision }}</td>
            <td>{{ h.state === "PUBLISHED" ? "已发布" : "草稿" }}</td>
            <td>
              <el-button
                :disabled="dirty"
                @click="inspect(h.id, h.state === 'PUBLISHED')"
                >{{
                  h.state === "PUBLISHED" ? "复制为新草稿 / 回滚" : "加载草稿"
                }}</el-button
              >
            </td>
          </tr>
        </tbody>
      </table></template
    >
    <footer class="master-footer">
      <span>配置保存不会立即影响正在编辑的商品</span
      ><input
        v-model="reason"
        placeholder="配置修订说明"
        aria-label="配置修订说明"
      /><el-button
        type="primary"
        :loading="busy"
        :disabled="!can('catalog.configure')"
        @click="save"
        >保存草稿并预览影响</el-button
      >
    </footer>
    <el-dialog
      v-model="previewOpen"
      title="跨品类固定布局预览"
      width="min(1200px,96vw)"
      :close-on-click-modal="false"
      ><div class="master-search">
        <select v-model="profile">
          <option v-for="(r, p) in draft.rules" :key="p" :value="p">
            {{ draft.nodes.find((n) => n.field_profile_id === p)?.name }}
          </option></select
        ><select v-model="group">
          <option v-for="t in draft.tabs" :key="t.id" :value="t.id">
            {{ t.label }}
          </option></select
        ><span>可选择子类型查看角色名称；这里只修改预览示例。</span>
      </div>
      <MasterFields
        v-if="previewData"
        :data="previewData"
        :profile="profile"
        :tab="group"
        :values="previewValues"
        @change="(k, v) => (previewValues[k] = v)"
    /></el-dialog>
    <el-dialog
      v-model="codeOpen"
      title="商品编码自动生成规则"
      width="900px"
      :close-on-click-modal="false"
      destroy-on-close
      ><ProductCodeRules
        :category="profile.toUpperCase()"
        @failure="emit('failure', $event)"
    /></el-dialog>
  </section>
</template>
<style scoped>
.master-config .dictionary-conflict {
  color: var(--el-color-danger-dark-2);
}
.master-config {
  background: white;
  border: 1px solid #dce5ef;
  border-radius: 7px;
  overflow: hidden;
  color: #284057;
}
.master-config small {
  display: block;
  color: #8192a3;
  font-size: 11px;
  margin-top: 5px;
}
.master-config input:not([type="checkbox"]),
.master-config select {
  padding: 7px 9px;
  border: 1px solid #ccd8e5;
  border-radius: 4px;
  font: inherit;
  font-size: 12px;
  max-width: 240px;
}
.master-config-note {
  padding: 16px 20px;
  color: #6c8197;
  font-size: 13px;
  line-height: 1.7;
}
.master-config .master-list-table {
  font-size: 12px;
}
</style>
