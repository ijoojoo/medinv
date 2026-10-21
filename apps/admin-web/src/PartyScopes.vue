<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import {
  clone,
  certificateTypes,
  namesFor,
  certificateGroupsText,
} from "./qualification";
import {
  channelNames,
  subjectNames,
  splitCodes,
  type BusinessScope,
  type QualificationSettings,
} from "./qualification";
const props = defineProps<{
  party: string;
  active: boolean;
  version: string;
}>();
const emit = defineEmits<{
  failure: [error: unknown];
  saved: [];
  editing: [value: boolean];
  review: [];
}>();
interface ScopeData {
  version: string;
  payload: { scopes: BusinessScope[] };
  active_payload: { scopes: BusinessScope[] };
  has_changes: boolean;
  legacy: boolean;
  party_kind: string;
  rules: QualificationSettings;
  results: {
    id: string;
    reasons: Record<string, string[]>;
    qualified: boolean;
  }[];
}
interface Cert {
  id: string;
  number: string;
  certificate_type: string;
  validity: string;
  state: string;
  valid_until: string | null;
}
const data = ref<ScopeData>(),
  scopes = ref<BusinessScope[]>([]),
  certs = ref<Cert[]>([]);
const editing = ref<BusinessScope>(),
  reason = ref(""),
  baseline = ref(""),
  loading = ref(false);
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () => JSON.stringify(scopes.value) !== baseline.value && !!data.value,
);
const root = computed(() => `/api/v1/catalog/parties/${props.party}/scopes`);
watch(
  () => dirty.value || !!editing.value || busy.value,
  (v) => emit("editing", v),
);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value || !!editing.value, busy: busy.value }),
    "parties",
  ),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const token = ++epoch;
  loading.value = true;
  try {
    const [result, certificates] = await Promise.all([
      request<ScopeData>(root.value),
      request<{ items: Cert[] }>(
        `/api/v1/catalog/parties/${props.party}/certificates`,
      ),
    ]);
    if (token !== epoch) return;
    data.value = result;
    scopes.value = result.payload.scopes;
    certs.value = certificates.items;
    baseline.value = JSON.stringify(scopes.value);
    reason.value = "";
  } catch (e) {
    if (token === epoch) emit("failure", e);
  } finally {
    if (token === epoch) loading.value = false;
  }
}
function edit(scope?: BusinessScope) {
  editing.value = clone(
    scope || {
      id: crypto.randomUUID(),
      category: "DEVICE",
      name: "",
      device_class: "I",
      directory_version: "2017",
      codes: [],
      excluded_codes: [],
      include_children: true,
      subject_type: "DISTRIBUTOR",
      channels: [
        data.value?.party_kind === "CUSTOMER" ? "WHOLESALE" : "PURCHASE",
      ],
      certificate_ids: [],
      exceptions: [],
      enabled: true,
      note: "",
    },
  );
}
function apply() {
  if (!editing.value) return;
  if (editing.value.category === "DRUG") {
    editing.value.device_class = null;
    editing.value.codes = [];
    editing.value.excluded_codes = [];
  }
  const index = scopes.value.findIndex((s) => s.id === editing.value!.id);
  if (index < 0) scopes.value.push(editing.value);
  else scopes.value[index] = editing.value;
  editing.value = undefined;
}
async function save() {
  await execute(
    root.value,
    {
      expected_version: data.value!.version,
      payload: { scopes: scopes.value },
      reason: reason.value,
    },
    async () => {
      await load();
      emit("saved");
    },
  );
}
const requirements = computed(
  () =>
    data.value?.rules.rules.filter(
      (r) =>
        editing.value &&
        r.enabled &&
        r.category === editing.value.category &&
        r.subjects.includes(editing.value.subject_type) &&
        (!r.device_classes.length ||
          r.device_classes.includes(editing.value.device_class || "")) &&
        r.channels.some((c) => editing.value!.channels.includes(c)),
    ) || [],
);
function reasons(id: string) {
  return [
    ...new Set(
      Object.values(
        data.value?.results.find((r) => r.id === id)?.reasons || {},
      ).flat(),
    ),
  ];
}
watch(
  () => [props.active, props.version],
  () => {
    if (props.active && !dirty.value && !editing.value && !busy.value)
      void load();
  },
);
onMounted(load);
</script>
<template>
  <section class="party-scopes" aria-label="经营范围与准入" v-loading="loading">
    <div class="master-toolbar">
      <div>
        <h3>经营范围与准入</h3>
        <p class="form-note">
          范围和证件随往来单位一次审核；已审核资料的变更通过后统一生效。
        </p>
      </div>
      <el-button v-if="can('catalog.write')" @click="edit()"
        >新增经营范围</el-button
      ><el-button @click="emit('review')">查看主体审核</el-button>
    </div>
    <p v-if="data?.legacy" class="form-note">
      尚未启用按类目管理。核对证件并保存范围后，提交一次主体审核；审核通过前保留当前有效资料。
    </p>
    <p v-else-if="data?.has_changes" class="form-note">
      有待审核变更。以下为变更预检，当前业务继续使用上次审核通过的资料。
    </p>
    <p v-if="data" class="form-note">
      异常处理：{{
        { BLOCK: "阻止办理", WARN: "仅提醒", OFF: "不自动拦截" }[
          data.rules.enforcement
        ]
      }}
      · 影响范围：{{
        data.rules.impact === "CATEGORY" ? "对应经营类目" : "整个往来单位"
      }}。可在企业设置 → 质量与准入修改。
    </p>
    <div class="table-scroll">
      <table class="business-table">
        <thead>
          <tr>
            <th>经营范围 / 类目</th>
            <th>单位身份 / 业务</th>
            <th>证件依据</th>
            <th>资料预检</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="scope in scopes" :key="scope.id">
            <td>
              <strong>{{ scope.name }}</strong
              ><br /><small
                >{{
                  scope.category === "DEVICE"
                    ? `${scope.device_class} 类 · ${scope.directory_version} 目录 · ${scope.codes.join("、")}`
                    : "药品范围"
                }}{{
                  scope.excluded_codes.length
                    ? "，排除 " + scope.excluded_codes.join("、")
                    : ""
                }}</small
              >
              <div v-if="!scope.enabled">已停用此范围</div>
            </td>
            <td>
              {{ subjectNames[scope.subject_type] }}<br /><small>{{
                namesFor(scope.channels, channelNames)
              }}</small>
            </td>
            <td>
              <div v-for="id in scope.certificate_ids" :key="id">
                {{ certs.find((c) => c.id === id)?.number || "证件待核对" }}
              </div>
              <span v-if="!scope.certificate_ids.length">尚未关联</span>
            </td>
            <td>
              <span v-if="dirty">待保存后预检</span
              ><template v-else
                ><span v-if="!reasons(scope.id).length">{{
                  scope.enabled ? "已配置条件满足" : "已停用"
                }}</span>
                <div
                  v-for="r in reasons(scope.id)"
                  :key="r"
                  class="scope-issue"
                >
                  {{ r }}
                </div></template
              >
            </td>
            <td>
              <el-button
                link
                :disabled="!can('catalog.write')"
                @click="edit(scope)"
                >编辑</el-button
              ><el-button
                link
                :disabled="!can('catalog.write')"
                @click="scope.enabled = !scope.enabled"
                >{{ scope.enabled ? "停用" : "启用" }}</el-button
              >
            </td>
          </tr>
          <tr v-if="!scopes.length">
            <td colspan="5">尚未登记经营范围。先新增范围，再关联所需证件。</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="qualification-actions" v-if="can('catalog.write')">
      <input
        v-model="reason"
        placeholder="范围变更说明"
        aria-label="范围变更说明"
        maxlength="200"
      /><el-button
        type="primary"
        :disabled="!dirty || !reason || busy"
        @click="save"
        >保存范围变更</el-button
      >
    </div>
    <el-dialog
      :model-value="!!editing"
      title="维护经营范围"
      width="800px"
      :close-on-click-modal="false"
      @close="editing = undefined"
    >
      <form v-if="editing" class="scope-form" @submit.prevent="apply">
        <label
          >范围名称<input
            v-model="editing.name"
            required
            placeholder="例如第三类器械 06 医用成像器械"
            maxlength="200" /></label
        ><label
          >商品大类<select v-model="editing.category">
            <option value="DEVICE">医疗器械</option>
            <option value="DRUG">药品</option>
          </select></label
        >
        <template v-if="editing.category === 'DEVICE'"
          ><label
            >器械管理类别<select v-model="editing.device_class">
              <option value="I">第一类</option>
              <option value="II">第二类</option>
              <option value="III">第三类</option>
            </select></label
          ><label
            >分类目录版本<input
              v-model="editing.directory_version"
              required
              placeholder="2017"
              maxlength="30" /></label
          ><label
            >允许分类编码<input
              :value="editing.codes.join('，')"
              @change="
                editing.codes = splitCodes(
                  ($event.target as HTMLInputElement).value,
                )
              "
              required
              placeholder="06，08-01（多个编码以逗号分隔）" /></label
          ><label
            >排除分类编码<input
              :value="editing.excluded_codes.join('，')"
              @change="
                editing.excluded_codes = splitCodes(
                  ($event.target as HTMLInputElement).value,
                )
              "
              placeholder="按许可证限制填写，可空" /></label
          ><label
            ><input
              type="checkbox"
              v-model="editing.include_children"
            />覆盖所填编码下的子类（扣除排除项）</label
          ></template
        >
        <label
          >单位业务身份<select v-model="editing.subject_type">
            <option v-for="(name, key) in subjectNames" :key="key" :value="key">
              {{ name }}
            </option>
          </select></label
        ><label
          >适用业务<el-select v-model="editing.channels" multiple
            ><el-option
              v-for="(name, key) in channelNames"
              :key="key"
              :label="name"
              :value="key" /></el-select
        ></label>
        <div class="scope-full">
          <strong>对应证件要求</strong>
          <div v-for="r in requirements" :key="r.id">
            <b>{{ r.name }}</b
            >：{{
              certificateGroupsText(r.certificate_groups) || "无额外证件要求"
            }}<small> · {{ r.article }}</small>
          </div>
          <p class="form-note">
            一张证件可关联多个范围。若范围不完整，请按原件拆分；系统不会推测许可证覆盖内容。
          </p>
        </div>
        <label class="scope-full"
          >关联本单位证件<el-select
            v-model="editing.certificate_ids"
            multiple
            filterable
            placeholder="先在证件资料中录入，再选择关联"
            ><el-option
              v-for="c in certs"
              :key="c.id"
              :value="c.id"
              :label="`${certificateTypes[c.certificate_type]} · ${c.number}${c.valid_until ? ' · 截止 ' + c.valid_until : ''}`" /></el-select
        ></label>
        <details class="scope-full">
          <summary>免许可 / 免备案等例外依据</summary>
          <p class="form-note">
            仅对指定规则登记具体适用依据，并随主体审核；不自动认定所有产品均符合例外。
          </p>
          <div
            v-for="(e, i) in editing.exceptions"
            :key="i"
            class="scope-exception"
          >
            <select v-model="e.rule_id" required>
              <option value="">选择适用规则</option>
              <option v-for="r in data?.rules.rules" :key="r.id" :value="r.id">
                {{ r.name }}
              </option></select
            ><input
              v-model="e.evidence_ref"
              required
              placeholder="核验凭据"
            /><input
              v-model="e.reason"
              required
              placeholder="具体适用理由及产品限制"
            /><el-button @click="editing.exceptions.splice(i, 1)"
              >移除</el-button
            >
          </div>
          <el-button
            @click="
              editing.exceptions.push({
                rule_id: '',
                evidence_ref: '',
                reason: '',
              })
            "
            >添加例外依据</el-button
          >
        </details>
        <label class="scope-full"
          >范围说明<textarea
            v-model="editing.note"
            maxlength="1000"
            placeholder="原件限制、产品归属、特殊适用条件等"
          />
        </label>
        <div class="qualification-actions scope-full">
          <el-button @click="editing = undefined">取消</el-button
          ><el-button type="primary" native-type="submit"
            >应用到范围草稿</el-button
          >
        </div>
      </form>
    </el-dialog>
  </section>
</template>
<style scoped>
.party-scopes {
  padding: 18px;
}
.party-scopes .business-table {
  min-width: 900px;
}
.scope-issue {
  color: var(--ui-warning, #a65b13);
  max-width: 340px;
}
.scope-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.scope-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.scope-full {
  grid-column: 1/-1;
}
.scope-form .el-select {
  width: 100%;
}
.scope-exception {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0;
}
.scope-exception input {
  min-width: 140px;
  flex: 1;
}
@media (max-width: 640px) {
  .scope-form {
    grid-template-columns: 1fr;
  }
  .party-scopes {
    padding: 10px;
  }
}
</style>
