<script setup lang="ts">
import DateInput from "./DateInput.vue";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { request, session } from "./api";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import CatalogHistory, { type CatalogEvent } from "./CatalogHistory.vue";
import {
  partyCertificateNames,
  partyCertificateLabels as label,
  partyCertificateValues,
  partyCertificateValue,
  emptyPartyCertificate,
  type PartyCertificateRecord,
} from "./partyCertificates";
const props = defineProps<{ party: string; active: boolean }>();
const emit = defineEmits<{
  failure: [error: unknown];
  editing: [value: boolean];
  saved: [];
}>();
interface Listing {
  items: PartyCertificateRecord[];
  types: Record<string, string>;
  business_uses: Record<string, string>;
  legacy: {
    number: string;
    raw_valid_from: string | null;
    raw_valid_until: string | null;
    source_version: string;
  };
}
interface Detail {
  record: PartyCertificateRecord;
  history: CatalogEvent[];
  has_more: boolean;
}
const root = computed(
  () => `/api/v1/catalog/parties/${props.party}/certificates`,
);
const data = ref<Listing | null>(null),
  detail = ref<Detail | null>(null),
  loading = ref(false);
const mode = ref<"create" | "edit" | "review" | null>(null),
  values = ref(emptyPartyCertificate());
const baseline = ref(""),
  reason = ref(""),
  decision = ref("VERIFY"),
  evidence = ref("");
const historyOffset = ref(0);
const { busy, execute } = useCommand((e) => emit("failure", e));
const dirty = computed(
  () =>
    !!mode.value &&
    (!!reason.value ||
      !!evidence.value ||
      (mode.value !== "review" &&
        JSON.stringify(partyCertificateValues(values.value)) !==
          baseline.value)),
);
watch(
  () => dirty.value || busy.value,
  (value) => emit("editing", value),
);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    "parties",
  ),
);
let epoch = 0;
onUnmounted(() => epoch++);
const canVerify = computed(
  () =>
    !!detail.value &&
    detail.value.record.state === "DRAFT" &&
    detail.value.record.created_by !== session.value?.principal_id &&
    !detail.value.record.revision_editors.includes(
      session.value?.principal_id || "",
    ),
);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<Listing>(root.value);
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
async function select(id: string, offset = 0) {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<Detail>(
      `${root.value}/${id}?offset=${offset}`,
    );
    if (current !== epoch) return;
    detail.value = result;
    historyOffset.value = offset;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function start(action: "create" | "edit" | "review") {
  mode.value = action;
  reason.value = "";
  evidence.value = "";
  values.value =
    action === "edit" && detail.value
      ? partyCertificateValues(detail.value.record)
      : emptyPartyCertificate(Object.keys(data.value?.types || {})[0]);
  if (action === "create")
    values.value.business_uses = Object.keys(data.value?.business_uses || {});
  baseline.value = JSON.stringify(partyCertificateValues(values.value));
  decision.value = canVerify.value ? "VERIFY" : "INACTIVATE";
}
async function cancel() {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm("放弃尚未保存的证照修改？", "退出证照编辑", {
        confirmButtonText: "放弃修改",
        cancelButtonText: "继续编辑",
        closeOnClickModal: false,
      });
    } catch {
      return;
    }
  }
  mode.value = null;
}
async function save() {
  const action = mode.value;
  if (!action || (action !== "create" && !detail.value)) return;
  const path =
    action === "create"
      ? root.value
      : `${root.value}/${detail.value!.record.id}/${action === "edit" ? "revise" : "review"}`;
  const payload =
    action === "review"
      ? {
          expected_version: detail.value!.record.version,
          decision: decision.value,
          evidence_ref: evidence.value,
          reason: reason.value,
        }
      : {
          ...partyCertificateValues(values.value),
          reason: reason.value,
          ...(action === "edit"
            ? { expected_version: detail.value!.record.version }
            : {}),
        };
  await execute<PartyCertificateRecord>(path, payload, async (result) => {
    mode.value = null;
    await load();
    await select(result.id);
    emit("saved");
  });
}
watch(
  () => props.active,
  (active) => {
    if (active && !mode.value && !busy.value) void load();
  },
);
onMounted(load);
</script>
<template>
  <section
    class="certificate-card"
    aria-label="往来单位证照与授权"
    v-loading="loading"
  >
    <div class="certificate-title">
      <div>
        <h3>往来单位证照与授权</h3>
        <span class="form-note">按原件维护多份证照，核验与有效期分别显示</span>
      </div>
      <el-button
        v-if="can('catalog.write')"
        type="primary"
        plain
        :disabled="!!mode || busy || loading || !data"
        @click="start('create')"
        >新增证照</el-button
      >
    </div>
    <p class="form-note">
      证照和授权按当前经营主体独立维护。资料核验与有效期分别显示；购销准入仍按上方单位资质审核结果校验。
    </p>
    <details v-if="data?.legacy.number" class="legacy-certificate">
      <summary>原有单证字段 · {{ data.legacy.number }} · 类型待核对</summary>
      <p>
        原始资质日期：{{ data.legacy.raw_valid_from || "未填写" }} 至
        {{
          data.legacy.raw_valid_until || "未填写"
        }}。此处原样保留；不会据此推断证照类型或签发日期。请查阅原件后新增证照。
      </p>
    </details>
    <div class="table-scroll">
      <table class="business-table certificate-table">
        <thead>
          <tr>
            <th>类型 / 编号</th>
            <th>证照主体 / 授权联系人</th>
            <th>适用业务</th>
            <th>核验状态</th>
            <th>有效期情况</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data?.items || []" :key="row.id">
            <td>
              <b>{{ label[row.certificate_type] }}</b
              ><small>{{ row.number }}</small>
            </td>
            <td>
              {{ row.holder_name || "待补齐"
              }}<small v-if="row.certificate_type === 'AUTHORIZATION'"
                >{{ row.authorized_name || "联系人待补齐" }} ·
                {{ row.authorized_phone || "电话待补齐" }}</small
              >
            </td>
            <td>
              {{ partyCertificateValue(row.business_uses, "business_uses") }}
            </td>
            <td>
              {{ label[row.state] }} · v{{ row.version
              }}<small
                v-if="row.compatibility !== 'MATCH'"
                class="certificate-warning"
                >{{
                  row.compatibility === "UNCONFIRMED"
                    ? "供销身份待核定"
                    : label[row.compatibility]
                }}</small
              >
            </td>
            <td>
              {{ label[row.validity]
              }}<small v-if="row.validity_mode === 'FIXED'"
                >{{ row.valid_from }} 至 {{ row.valid_until }}</small
              >
            </td>
            <td>
              <el-button
                text
                :disabled="!!mode || busy || loading"
                @click="select(row.id)"
                >查看证照</el-button
              >
            </td>
          </tr>
          <tr v-if="data && !data.items.length">
            <td colspan="6" class="form-note">
              尚无独立证照档案，可先补录原件资料。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <el-button v-if="!data" :disabled="loading" @click="load"
      >重新加载证照</el-button
    >
    <section
      v-if="detail && !mode"
      class="certificate-detail"
      aria-label="证照详情"
    >
      <div class="certificate-title">
        <h4>{{ detail.record.number }} · 第 {{ detail.record.version }} 版</h4>
        <div>
          <el-button
            v-if="can('catalog.write')"
            :disabled="busy || loading"
            @click="start('edit')"
            >修订证照</el-button
          >
          <el-button
            v-if="can('catalog.review') && detail.record.state !== 'INACTIVE'"
            :disabled="busy || loading"
            @click="start('review')"
            >核验 / 停用</el-button
          >
          <el-button
            :disabled="busy || loading"
            @click="select(detail.record.id)"
            >刷新证照</el-button
          >
        </div>
      </div>
      <dl class="facts">
        <template v-for="(name, key) in partyCertificateNames" :key="key">
          <dt>{{ name }}</dt>
          <dd>
            {{
              ["valid_from", "valid_until"].includes(key) &&
              detail.record.validity_mode !== "FIXED"
                ? label[detail.record.validity_mode]
                : partyCertificateValue(
                    detail.record[key as keyof PartyCertificateRecord],
                    key,
                  )
            }}
          </dd>
        </template>
      </dl>
      <details>
        <summary>证照变更记录</summary>
        <CatalogHistory :events="detail.history" />
        <div class="pager">
          <el-button
            :disabled="loading || historyOffset === 0"
            @click="select(detail.record.id, historyOffset - 50)"
            >上一页证照记录</el-button
          >
          <el-button
            :disabled="loading || !detail.has_more"
            @click="select(detail.record.id, historyOffset + 50)"
            >下一页证照记录</el-button
          >
        </div>
      </details>
    </section>
    <form
      v-if="mode"
      class="business-form certificate-editor"
      aria-label="证照编辑"
      @submit.prevent="save"
    >
      <h4>
        {{
          mode === "create"
            ? "新增证照"
            : mode === "edit"
              ? "修订证照"
              : "证照核验 / 停用"
        }}
      </h4>
      <fieldset class="form-grid" :disabled="busy || loading">
        <template v-if="mode !== 'review'">
          <label
            >证照类型<select
              v-model="values.certificate_type"
              required
              @change="
                values.authorized_name = '';
                values.authorized_phone = '';
                values.authorization_duties = '';
              "
            >
              <option
                v-for="(name, key) in data?.types"
                :key="key"
                :value="key"
              >
                {{ name }}
              </option>
            </select></label
          >
          <label
            >证照编号<input v-model="values.number" required maxlength="120"
          /></label>
          <label
            >证照主体 / 授权单位<input
              v-model="values.holder_name"
              maxlength="200"
              placeholder="按原件登记，核验前补齐"
          /></label>
          <label
            >发证机关 / 签发单位<input
              v-model="values.issuer"
              maxlength="200"
              placeholder="按原件登记，核验前补齐"
          /></label>
          <label
            >签发日期<DateInput
              v-model="values.issued_on"
              :disabled="busy || loading"
          /></label>
          <label
            >有效期方式<select
              v-model="values.validity_mode"
              @change="
                values.valid_from = null;
                values.valid_until = null;
              "
            >
              <option value="UNCONFIRMED">待核实</option>
              <option value="FIXED">固定有效期</option>
              <option value="NOT_STATED">原件未载明有效期</option>
              <option value="LONG_TERM">原件载明长期</option>
            </select></label
          >
          <template v-if="values.validity_mode === 'FIXED'">
            <label
              >有效期起始日期<DateInput
                v-model="values.valid_from"
                required
                :max="values.valid_until || undefined"
                :disabled="busy || loading"
            /></label>
            <label
              >有效期截止日期<DateInput
                v-model="values.valid_until"
                required
                :min="values.valid_from || undefined"
                :disabled="busy || loading"
            /></label>
          </template>
          <fieldset class="full-width business-uses">
            <legend>适用业务（按本单位供销身份选择）</legend>
            <label v-for="use in ['SUPPLY', 'PURCHASE']" :key="use">
              <input
                type="checkbox"
                v-model="values.business_uses"
                :value="use"
                :disabled="
                  !data?.business_uses[use] &&
                  !values.business_uses.includes(use)
                "
              />
              {{ label[use] }}
            </label>
          </fieldset>
          <label class="full-width"
            >原件经营范围<textarea
              v-model="values.scope_text"
              maxlength="1000"
              rows="2"
              placeholder="按原件记录；不从自由文本自动推断经营准入"
            />
          </label>
          <template v-if="values.certificate_type === 'AUTHORIZATION'">
            <label
              >授权联系人<input
                v-model="values.authorized_name"
                maxlength="100"
            /></label>
            <label
              >授权联系电话<input
                v-model="values.authorized_phone"
                maxlength="50"
            /></label>
            <label class="full-width"
              >授权事项<textarea
                v-model="values.authorization_duties"
                maxlength="500"
                rows="2"
                placeholder="如业务洽谈、订货、收货或结算，按授权原件填写"
              />
            </label>
          </template>
          <label class="full-width"
            >原件凭据编号<input
              v-model="values.evidence_ref"
              maxlength="120"
              placeholder="纸质档案号或内部文档编号，核验前补齐"
          /></label>
          <label class="full-width"
            >证照备注<textarea v-model="values.note" maxlength="500" rows="2" />
          </label>
        </template>
        <template v-else>
          <p class="full-width form-note">
            {{ detail?.record.number }} ·
            核验资料内容与原件是否一致，有效期单独显示。{{
              canVerify ? "" : "仅独立于建档及本轮修订的人员可以核验通过。"
            }}
          </p>
          <label
            >证照处理<select v-model="decision">
              <option value="VERIFY" :disabled="!canVerify">核验通过</option>
              <option value="INACTIVATE">停用证照</option>
            </select></label
          >
          <label
            >核验凭据编号<input v-model="evidence" required maxlength="120"
          /></label>
        </template>
        <label class="full-width"
          >证照操作说明<input v-model="reason" required maxlength="200"
        /></label>
      </fieldset>
      <div class="form-actions">
        <span class="form-note">{{
          mode === "review"
            ? "仅更新本证照的核验状态。"
            : "保存后为待核验，变更保留前后版本记录。"
        }}</span>
        <el-button :disabled="busy || loading" @click="cancel"
          >取消证照编辑</el-button
        >
        <el-button
          type="primary"
          native-type="submit"
          :loading="busy"
          :disabled="
            loading ||
            !dirty ||
            (mode !== 'review' && !values.business_uses.length)
          "
          >{{ mode === "review" ? "提交证照核验" : "保存证照草稿" }}</el-button
        >
      </div>
    </form>
  </section>
</template>
<style scoped>
.business-uses {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  padding: 12px;
  border: 1px solid var(--ui-border);
}
.business-uses label {
  display: flex;
  align-items: center;
  gap: 8px;
}
.business-uses input {
  width: auto;
}
.certificate-card {
  margin-top: 20px;
  border-top: 1px solid var(--ui-border);
  padding-top: 20px;
}
.certificate-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
h3,
h4 {
  margin: 0 0 6px;
}
.legacy-certificate {
  background: var(--ui-tint);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 10px 12px;
  margin: 12px 0;
  font-size: 13px;
}
summary {
  cursor: pointer;
}
.certificate-table {
  min-width: 760px;
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}
.certificate-table th {
  background: var(--ui-tint);
  color: var(--ui-text);
  font-weight: 600;
}
.certificate-table th,
.certificate-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--ui-border);
}
.certificate-table tbody tr:hover {
  background: var(--ui-soft);
}
.table-scroll {
  overflow-x: auto;
}
.certificate-table td {
  vertical-align: top;
  overflow-wrap: anywhere;
  max-width: 300px;
}
.certificate-table small {
  display: block;
  margin-top: 4px;
  color: var(--ui-muted);
}
.certificate-table .certificate-warning {
  color: var(--el-color-warning-dark-2);
}
.certificate-editor,
.certificate-detail {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: var(--ui-surface, #fff);
}
fieldset {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
.certificate-detail :deep(dd) {
  overflow-wrap: anywhere;
}
</style>
