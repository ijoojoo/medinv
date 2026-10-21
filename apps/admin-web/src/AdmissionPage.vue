<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, useCommand } from "./business";
import { activeEntity } from "./entityScope";
import { registerCatalogChanges } from "./catalogChanges";
import { localTime } from "./sales";
import { businessToday } from "./dates";
import AdmissionScopeRow from "./AdmissionScopeRow.vue";
import PartyLookup from "./PartyLookup.vue";
import {
  emptyAdmission,
  type AdmissionPolicy,
  type ScopeGrant,
} from "./admission";
import type { CatalogDictionary } from "./catalogDictionaries";
const emit = defineEmits<{ failure: [error: unknown] }>();
const channel = ref<"PURCHASE" | "WHOLESALE">("PURCHASE"),
  data = ref<AdmissionPolicy | null>(null),
  form = ref(emptyAdmission());
const dictionaries = ref<CatalogDictionary[]>([]),
  loading = ref(false),
  baseline = ref(""),
  reason = ref(""),
  reviewReason = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
const root = computed(
  () => `/api/v1/catalog/admission/${activeEntity.value}/${channel.value}`,
);
const dirty = computed(
  () =>
    !!data.value &&
    (JSON.stringify(form.value) !== baseline.value ||
      !!reason.value ||
      !!reviewReason.value),
);
const modified = computed(
  () => JSON.stringify(form.value) !== baseline.value || !!reason.value,
);
const independent = computed(
  () => !data.value?.editors.includes(session.value?.principal_id || ""),
);
const blocked = computed(() => loading.value || busy.value);
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: blocked.value }),
    "admission",
  ),
);
let epoch = 0;
onUnmounted(() => epoch++);
function reset() {
  if (!data.value) return;
  form.value = JSON.parse(JSON.stringify(data.value.payload));
  baseline.value = JSON.stringify(form.value);
  reason.value = "";
  reviewReason.value = "";
}
async function load() {
  const token = ++epoch;
  loading.value = true;
  result.value = null;
  try {
    const [policy, dicts] = await Promise.all([
      request<AdmissionPolicy>(root.value),
      request<{ items: CatalogDictionary[] }>("/api/v1/catalog/dictionaries"),
    ]);
    if (token !== epoch) return;
    data.value = policy;
    dictionaries.value = dicts.items;
    reset();
  } catch (e) {
    if (token === epoch) emit("failure", e);
  } finally {
    if (token === epoch) loading.value = false;
  }
}
function add(party: boolean) {
  const grant: ScopeGrant = {
    category: "DEVICE",
    value: "",
    valid_from: businessToday(),
    valid_until: "",
    evidence_ref: "",
    device_class: "III",
    directory_version: "2017",
    codes: [],
    excluded_codes: [],
    include_children: true,
  };
  if (party)
    Object.assign(grant, {
      party_id: "",
      certificate_id: "",
      certificate_version: "",
    });
  (party ? form.value.party_scopes : form.value.own_scopes).push(grant);
}
async function save() {
  if (!data.value) return;
  await execute(
    root.value,
    {
      expected_version: data.value.version,
      payload: form.value,
      reason: reason.value,
    },
    load,
  );
}
async function review() {
  if (!data.value) return;
  await execute(
    `${root.value}/review`,
    { expected_version: data.value.version, reason: reviewReason.value },
    load,
  );
}
const party = ref(""),
  search = ref(""),
  sku = ref(""),
  source = ref("ACTIVE"),
  searching = ref(false),
  checking = ref(false);
const products = ref<{ id: string; code: string; name: string }[]>([]),
  more = ref(false);
const result = ref<{
  enabled: boolean;
  passed: boolean;
  source: string;
  policy_version: string;
  message: string;
  issues: string[];
} | null>(null);
async function findProducts() {
  const token = epoch;
  searching.value = true;
  try {
    const response = await request<{
      items: typeof products.value;
      has_more: boolean;
    }>(`${root.value}/products?search=${encodeURIComponent(search.value)}`);
    if (token === epoch) {
      products.value = response.items;
      more.value = response.has_more;
      sku.value = "";
    }
  } catch (e) {
    if (token === epoch) emit("failure", e);
  } finally {
    if (token === epoch) searching.value = false;
  }
}
async function preview() {
  const token = epoch;
  checking.value = true;
  result.value = null;
  try {
    const response = await request<NonNullable<typeof result.value>>(
      `${root.value}/preview`,
      {
        method: "POST",
        body: JSON.stringify({
          party_id: party.value,
          sku_ids: [sku.value],
          source: source.value,
        }),
      },
    );
    if (token === epoch) result.value = response;
  } catch (e) {
    if (token === epoch) emit("failure", e);
  } finally {
    if (token === epoch) checking.value = false;
  }
}
function changeChannel() {
  party.value = "";
  sku.value = "";
  products.value = [];
  void load();
}
onMounted(load);
</script>

<template>
  <section class="admission-page" aria-label="经营范围与准入">
    <header class="admission-heading">
      <div>
        <h2>经营范围与准入</h2>
        <p>
          以原件核验结果配置普通药品、医疗器械范围。原件文字保留供核对，系统不自动推断经营资格。
        </p>
      </div>
      <label
        >适用业务<select
          v-model="channel"
          :disabled="dirty || blocked || checking || searching"
          @change="changeChannel"
        >
          <option value="PURCHASE">采购</option>
          <option value="WHOLESALE">批发销售</option>
        </select></label
      >
      <button type="button" :disabled="dirty || blocked" @click="load">
        刷新配置
      </button>
    </header>
    <p v-if="loading" role="status">正在加载准入配置…</p>
    <template v-if="data">
      <div class="admission-status" role="status">
        当前生效：<strong>{{
          data.active_payload.enabled ? "已启用结构化准入" : "未启用结构化准入"
        }}</strong>
        · 生效版本 {{ data.active_version }} ·
        {{ data.has_draft ? `有待复核草稿 v${data.version}` : "没有待复核草稿"
        }}<br />此处配置本企业范围并进行交易试算；往来单位范围随单位资料统一审核。
      </div>
      <form @submit.prevent="save" aria-label="准入配置草稿">
        <fieldset :disabled="blocked || !can('catalog.configure')">
          <legend>配置草稿</legend>
          <label class="admission-switch"
            ><input
              type="checkbox"
              v-model="form.enabled"
            />启用本业务的结构化范围校验</label
          >
          <p>
            启用后，每个商品的范围必须同时匹配本企业与往来单位的核验记录。核验有效期含起止日；药品按统一范围匹配；器械可按管理类别、目录版本及分类编码匹配。
          </p>
          <h3>本企业范围</h3>
          <p v-if="!form.own_scopes.length">尚未配置本企业范围。</p>
          <AdmissionScopeRow
            v-for="(grant, i) in form.own_scopes"
            :key="i"
            :grant="grant"
            :entity="activeEntity"
            :channel="channel"
            :dictionaries="dictionaries"
            :disabled="blocked || !can('catalog.configure')"
            @remove="form.own_scopes.splice(i, 1)"
            @failure="emit('failure', $event)"
          />
          <button
            v-if="can('catalog.configure')"
            type="button"
            :disabled="form.own_scopes.length >= 200"
            @click="add(false)"
          >
            添加本企业范围
          </button>
          <p class="form-note">
            往来单位的经营范围请在“往来单位 →
            经营范围与准入”维护，随单位资料一次审核。原有集中配置保留历史兼容，不在此重复编辑。
          </p>
          <div v-if="can('catalog.configure')" class="admission-actions">
            <label
              >配置原因<input
                v-model="reason"
                required
                maxlength="200" /></label
            ><button type="submit" :disabled="!modified">保存草稿</button
            ><button type="button" :disabled="!dirty" @click="reset">
              放弃本次修改
            </button>
          </div>
        </fieldset>
      </form>
      <details class="admission-live">
        <summary>对照当前生效范围 · v{{ data.active_version }}</summary>
        <p>{{ data.active_payload.enabled ? "已启用" : "未启用" }}</p>
        <h3>本企业范围</h3>
        <AdmissionScopeRow
          v-for="(grant, i) in data.active_payload.own_scopes"
          :key="i"
          :grant="grant"
          :entity="activeEntity"
          :channel="channel"
          :dictionaries="dictionaries"
          disabled
          @failure="emit('failure', $event)"
        />
        <h3>往来单位范围</h3>
        <AdmissionScopeRow
          v-for="(grant, i) in data.active_payload.party_scopes"
          :key="i"
          :grant="grant"
          party
          :entity="activeEntity"
          :channel="channel"
          :dictionaries="dictionaries"
          disabled
          @failure="emit('failure', $event)"
        />
      </details>
      <form
        v-if="can('catalog.review') && data.has_draft"
        @submit.prevent="review"
        class="admission-review"
        aria-label="独立复核准入配置"
      >
        <p>
          {{
            independent
              ? "请核对草稿、本企业凭据及往来单位原件，再发布此配置。"
              : "你参与了本次草稿修改，须由其他人员独立复核。"
          }}
        </p>
        <label
          >复核意见<input
            v-model="reviewReason"
            required
            maxlength="200"
            :disabled="!independent || blocked || modified" /></label
        ><button type="submit" :disabled="!independent || blocked || modified">
          复核并生效
        </button>
      </form>
      <section class="admission-preview" aria-label="准入试算">
        <h3>准入试算</h3>
        <p>试算使用已保存的草稿或当前生效配置；结果仅供排查范围问题。</p>
        <form class="admission-fields" @submit.prevent="findProducts">
          <label>检索试算商品<input v-model="search" maxlength="100" /></label
          ><button :disabled="searching || blocked">查找商品</button
          ><span v-if="more">仅显示前 50 条，请缩小检索范围。</span>
        </form>
        <form class="admission-fields" @submit.prevent="preview">
          <PartyLookup
            v-model="party"
            :entity="activeEntity"
            :channel="channel === 'PURCHASE' ? 'purchase' : 'sale'"
            label="试算往来单位"
            :qualified-only="false"
            required
          />
          <label
            >试算商品<select v-model="sku" required>
              <option value="" disabled>先查找商品</option>
              <option v-for="p in products" :key="p.id" :value="p.id">
                {{ p.code }} · {{ p.name }}
              </option>
            </select></label
          >
          <label
            >试算依据<select v-model="source">
              <option value="ACTIVE">当前生效配置</option>
              <option value="DRAFT">已保存草稿</option>
            </select></label
          ><button
            :disabled="checking || blocked || !party || !sku || modified"
          >
            检查范围
          </button>
        </form>
        <div v-if="result" class="admission-result" role="status">
          <strong>{{
            !result.enabled
              ? "结构化准入未启用"
              : result.passed
                ? "范围匹配通过"
                : "范围匹配未通过"
          }}</strong>
          · {{ result.source === "DRAFT" ? "草稿" : "生效" }}版本
          {{ result.policy_version }}
          <p>{{ result.message }}</p>
          <ul v-if="result.issues.length">
            <li v-for="issue in result.issues" :key="issue">{{ issue }}</li>
          </ul>
        </div>
      </section>
      <details>
        <summary>最近 30 次配置与复核记录</summary>
        <ul>
          <li v-for="(h, i) in data.history" :key="i">
            {{ localTime(h.created_at) }} ·
            {{ h.action.endsWith("published") ? "复核生效" : "保存草稿" }} v{{
              h.detail.after.version
            }}
            · {{ h.detail.reason }}
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>

<style>
.admission-page {
  display: grid;
  gap: 18px;
  padding: 20px;
  max-width: 1500px;
  margin: auto;
}
.admission-page h2,
.admission-page h3 {
  margin: 0 0 10px;
}
.admission-page p {
  color: #526176;
  font-size: 13px;
  line-height: 1.7;
}
.admission-heading,
.admission-fields,
.admission-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 12px;
}
.admission-heading > div {
  flex: 1;
  min-width: 300px;
}
.admission-page label:not(.admission-switch) {
  display: grid;
  gap: 5px;
  font-size: 13px;
}
.admission-page input:not([type="checkbox"]),
.admission-page select {
  min-height: 34px;
  max-width: 350px;
  padding: 5px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 5px;
  background: white;
  color: #24354b;
}
.admission-page button {
  min-height: 34px;
  padding: 5px 12px;
}
.admission-page fieldset,
.admission-preview,
.admission-review,
.admission-live {
  border: 1px solid #d7e0ea;
  border-radius: 8px;
  padding: 18px;
  background: white;
  min-width: 0;
}
.admission-page fieldset > h3 {
  margin-top: 22px;
}
.admission-grant {
  background: #f6f8fb;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  margin: 10px 0;
}
.admission-status,
.admission-result {
  padding: 14px 18px;
  background: #edf5ff;
  border-left: 3px solid #356dc2;
  font-size: 14px;
  line-height: 1.8;
}
.admission-actions,
.admission-preview form {
  margin-top: 15px;
}
.admission-review {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 12px;
}
.admission-review p {
  flex-basis: 100%;
  margin: 0;
}
.admission-page .error {
  color: #b42318;
}
.admission-page summary {
  cursor: pointer;
  font-weight: 600;
}
</style>
