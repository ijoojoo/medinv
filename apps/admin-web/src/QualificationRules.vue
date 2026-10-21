<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import { certificateLabels } from "./productCertificates";
const productCertificateTypes = {
  ...Object.fromEntries(
    Object.entries(certificateLabels).filter(([k]) =>
      ["DRUG_APPROVAL", "DEVICE_REGISTRATION", "DEVICE_FILING"].includes(k),
    ),
  ),
  HEALTH_REGISTRATION: "保健食品注册",
  HEALTH_FILING: "保健食品备案",
  FSMP_REGISTRATION: "特医食品注册",
  INFANT_FORMULA_REGISTRATION: "婴幼儿配方注册",
  COSMETIC_FILING: "普通化妆品备案",
  COSMETIC_REGISTRATION: "特殊化妆品注册",
};
import {
  clone,
  certificateTypes,
  namesFor,
  certificateGroupsText,
} from "./qualification";
import {
  subjectNames,
  channelNames,
  type QualificationRule,
  type QualificationSettings,
} from "./qualification";
const emit = defineEmits<{ failure: [error: unknown] }>();
const values = ref<QualificationSettings>(),
  defaults = ref<QualificationSettings>();
const version = ref("0"),
  reason = ref(""),
  baseline = ref(""),
  loading = ref(false);
const editing = ref<QualificationRule>(),
  history = ref<
    { created_at: string; detail: { reason: string; version: string } }[]
  >([]);
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () => !!values.value && JSON.stringify(values.value) !== baseline.value,
);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value || !!editing.value, busy: busy.value }),
    "enterprise-settings",
  ),
);
async function load() {
  loading.value = true;
  try {
    const result = await request<{
      payload: QualificationSettings;
      defaults: QualificationSettings;
      version: string;
      history: typeof history.value;
    }>("/api/v1/catalog/qualification-rules");
    values.value = result.payload;
    defaults.value = result.defaults;
    version.value = result.version;
    history.value = result.history;
    baseline.value = JSON.stringify(values.value);
    reason.value = "";
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
}
function edit(rule?: QualificationRule) {
  editing.value = clone(
    rule || {
      ...defaults.value!.rules[0],
      id: crypto.randomUUID(),
      name: "新增准入规则",
      certificate_groups: [],
      note: "",
    },
  );
}
function apply() {
  if (!editing.value || !values.value) return;
  const index = values.value.rules.findIndex((r) => r.id === editing.value!.id);
  if (index < 0) values.value.rules.push(editing.value);
  else values.value.rules[index] = editing.value;
  editing.value = undefined;
}
async function save() {
  await execute(
    "/api/v1/catalog/qualification-rules",
    {
      expected_version: version.value,
      payload: values.value,
      reason: reason.value,
    },
    load,
  );
}
onMounted(load);
</script>
<template>
  <section
    class="qualification-settings"
    aria-label="质量与准入规则"
    v-loading="loading"
  >
    <div class="master-toolbar">
      <div>
        <h3>质量与准入规则</h3>
        <p class="form-note">
          内置规则是可修改的起点。修改记录保存版本、原因和依据；不会自动覆盖企业配置。
        </p>
      </div>
      <el-tag>第 {{ version }} 版</el-tag>
    </div>
    <template v-if="values">
      <fieldset
        :disabled="!can('catalog.configure') || busy"
        class="qualification-controls"
      >
        <label
          >异常处理<select v-model="values.enforcement">
            <option value="BLOCK">阻止办理</option>
            <option value="WARN">仅提醒</option>
            <option value="OFF">不自动拦截</option>
          </select></label
        >
        <label
          >影响范围<select v-model="values.impact">
            <option value="CATEGORY">对应经营类目</option>
            <option value="PARTY">整个往来单位</option>
          </select></label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.require_own_scope"
          />同时检查本企业经营范围</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.legacy_party_validity"
          />未迁移单位继续检查旧资料有效期</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.product_certificate_required"
          />商品审核和交易要求适用产品证件</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.product_certificate_dates"
          />检查产品证件有效期</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.product_certificate_type_check"
          />检查产品证件类型与监管类别对应关系</label
        >
      </fieldset>
      <fieldset
        class="qualification-controls"
        :disabled="busy || !can('catalog.configure')"
      >
        <legend>随主体审核的材料检查</legend>
        <label
          ><input
            type="checkbox"
            v-model="values.product_regulatory_subject_required"
          />商品须关联监管主体</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.certificate_evidence_required"
          />证件须填写主体、签发机关和原件凭据</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.authorization_contact_required"
          />授权书须填写联系人、电话和授权事项</label
        >
        <label
          ><input
            type="checkbox"
            v-model="values.party_certificate_validity_confirmed"
          />单位证件须明确有效期方式</label
        >
        <label
          >要求固定起止日期的产品证件
          <el-select
            v-model="values.product_fixed_period_types"
            multiple
            :disabled="busy || !can('catalog.configure')"
            style="min-width: 280px"
          >
            <el-option
              v-for="(name, key) in productCertificateTypes"
              :key="key"
              :label="name"
              :value="key"
            />
          </el-select>
        </label>
      </fieldset>
      <p class="form-note">
        范围和证件随商品 /
        往来单位统一审核。关闭自动拦截仍保留真实资质状态；例外适用依据在具体单位范围内登记。
      </p>
      <div class="master-toolbar">
        <strong>证件要求 · {{ values.rules.length }} 条</strong
        ><el-button v-if="can('catalog.configure')" @click="edit()"
          >新增规则</el-button
        >
      </div>
      <div class="table-scroll">
        <table class="business-table">
          <thead>
            <tr>
              <th>规则 / 适用范围</th>
              <th>所需证件</th>
              <th>法规依据</th>
              <th>启用</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rule in values.rules" :key="rule.id">
              <td>
                <strong>{{ rule.name }}</strong
                ><br /><small
                  >{{ rule.category === "DEVICE" ? "医疗器械" : "药品" }}
                  {{ rule.device_classes.join("、") }} ·
                  {{ namesFor(rule.subjects, subjectNames) }}</small
                >
              </td>
              <td>
                <div v-for="(group, i) in rule.certificate_groups" :key="i">
                  {{ certificateGroupsText([group]) }}
                </div>
                <small v-if="!rule.certificate_groups.length"
                  >未要求额外证件</small
                >
              </td>
              <td>
                <a
                  :href="rule.source_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  >{{ rule.source_title }}</a
                ><br /><small
                  >{{ rule.article }} · 核对 {{ rule.checked_on }}</small
                >
              </td>
              <td>
                <input
                  type="checkbox"
                  :aria-label="`启用${rule.name}`"
                  v-model="rule.enabled"
                  :disabled="!can('catalog.configure')"
                />
              </td>
              <td>
                <el-button
                  link
                  @click="edit(rule)"
                  :disabled="!can('catalog.configure')"
                  >编辑</el-button
                >
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="can('catalog.configure')" class="qualification-actions">
        <el-button @click="values = clone(defaults!)"
          >恢复内置默认至草稿</el-button
        ><input
          v-model="reason"
          aria-label="规则变更原因"
          placeholder="填写规则变更原因"
          maxlength="200"
        /><el-button
          type="primary"
          :disabled="!dirty || !reason || busy"
          @click="save"
          >保存规则</el-button
        >
      </div>
      <details>
        <summary>规则变更记录</summary>
        <p v-if="!history.length">当前为内置默认配置，尚无企业修改。</p>
        <p v-for="(h, i) in history" :key="i">
          {{ h.created_at }} · 第 {{ h.detail.version }} 版 ·
          {{ h.detail.reason }}
        </p>
      </details>
    </template>
    <el-dialog
      :model-value="!!editing"
      title="编辑准入规则"
      width="760px"
      :close-on-click-modal="false"
      @close="editing = undefined"
    >
      <form
        v-if="editing"
        class="qualification-rule-form"
        @submit.prevent="apply"
      >
        <label
          >规则名称<input v-model="editing.name" required maxlength="200"
        /></label>
        <label
          >商品大类<select v-model="editing.category">
            <option value="DRUG">药品</option>
            <option value="DEVICE">医疗器械</option>
          </select></label
        >
        <label v-if="editing.category === 'DEVICE'"
          >器械管理类别（空表示全部）<el-select
            v-model="editing.device_classes"
            multiple
            ><el-option
              v-for="c in ['I', 'II', 'III']"
              :key="c"
              :value="c"
              :label="c + ' 类'" /></el-select
        ></label>
        <label
          >适用单位身份<el-select v-model="editing.subjects" multiple
            ><el-option
              v-for="(name, key) in subjectNames"
              :key="key"
              :value="key"
              :label="name" /></el-select
        ></label>
        <label
          >适用业务<el-select v-model="editing.channels" multiple
            ><el-option
              v-for="(name, key) in channelNames"
              :key="key"
              :value="key"
              :label="name" /></el-select
        ></label>
        <div class="qualification-rule-wide">
          <strong>证件条件</strong>
          <p class="form-note">每组至少满足一种；不同组需要同时满足。</p>
          <div
            v-for="(_, i) in editing.certificate_groups"
            :key="i"
            class="qualification-actions"
          >
            <el-select
              v-model="editing.certificate_groups[i]"
              multiple
              placeholder="本组任选一种证件"
              ><el-option
                v-for="(name, key) in certificateTypes"
                :key="key"
                :value="key"
                :label="name" /></el-select
            ><el-button @click="editing.certificate_groups.splice(i, 1)"
              >移除组</el-button
            >
          </div>
          <el-button @click="editing.certificate_groups.push([])"
            >添加证件条件组</el-button
          >
        </div>
        <label>依据文件<input v-model="editing.source_title" required /></label
        ><label
          >条款 / 适用说明<input v-model="editing.article" required
        /></label>
        <label class="qualification-rule-wide"
          >来源链接<input v-model="editing.source_url" type="url" required
        /></label>
        <label
          >规则生效日期<input
            type="date"
            v-model="editing.effective_from"
            required /></label
        ><label
          >规则失效日期（可空）<input
            type="date"
            :value="editing.effective_until || ''"
            @input="
              editing.effective_until =
                ($event.target as HTMLInputElement).value || null
            "
        /></label>
        <label
          >法规核对日期<input
            type="date"
            v-model="editing.checked_on"
            required /></label
        ><label>启用<input type="checkbox" v-model="editing.enabled" /></label>
        <label class="qualification-rule-wide"
          >说明<textarea v-model="editing.note" maxlength="1000" />
        </label>
        <div class="qualification-actions qualification-rule-wide">
          <el-button @click="editing = undefined">取消</el-button
          ><el-button type="primary" native-type="submit"
            >应用到规则草稿</el-button
          >
        </div>
      </form>
    </el-dialog>
  </section>
</template>
<style>
.qualification-controls,
.qualification-rule-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  border: 0;
  padding: 12px 0;
}
.qualification-controls label,
.qualification-rule-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.qualification-controls label:has(input[type="checkbox"]) {
  flex-direction: row;
  align-items: center;
}
.qualification-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 14px 0;
}
.qualification-actions > input {
  flex: 1;
  min-width: 180px;
}
.qualification-rule-wide {
  grid-column: 1/-1;
}
.qualification-rule-form .el-select {
  width: 100%;
}
.qualification-actions .el-select {
  flex: 1;
}
.qualification-settings .business-table {
  min-width: 760px;
}
.qualification-settings .form-note {
  margin: 6px 0 12px;
}
@media (max-width: 640px) {
  .qualification-controls,
  .qualification-rule-form {
    grid-template-columns: 1fr;
  }
}
</style>
