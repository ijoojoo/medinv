<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import PartyLookup from "./PartyLookup.vue";
import DateInput from "./DateInput.vue";
import AttachmentLink from "./AttachmentLink.vue";
import { partyCertificateLabels } from "./partyCertificates";
import type { CatalogDictionary } from "./catalogDictionaries";
import type { ScopeGrant } from "./admission";
const props = defineProps<{
  grant: ScopeGrant;
  party?: boolean;
  entity: string;
  channel: "PURCHASE" | "WHOLESALE";
  dictionaries: CatalogDictionary[];
  disabled: boolean;
}>();
const emit = defineEmits<{ remove: []; failure: [error: unknown] }>();
type Cert = {
  id: string;
  version: string;
  number: string;
  certificate_type: string;
  state: string;
  scope_text: string;
  validity_mode: string;
  valid_from: string | null;
  valid_until: string | null;
  attachment_id?: string;
};
const certs = ref<Cert[]>([]),
  loading = ref(false);
let epoch = 0;
onUnmounted(() => epoch++);
async function loadCertificates() {
  const id = props.grant.party_id;
  const token = ++epoch;
  certs.value = [];
  if (!id) {
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    const response = await request<{ items: Cert[] }>(
      `/api/v1/catalog/parties/${id}/certificates`,
    );
    if (token === epoch) certs.value = response.items;
  } catch (e) {
    if (token === epoch) emit("failure", e);
  } finally {
    if (token === epoch) loading.value = false;
  }
}
watch(() => [props.grant, props.grant.party_id], loadCertificates, {
  immediate: true,
});
const options = computed(
  () =>
    props.dictionaries
      .find(
        (d) =>
          d.key ===
          (props.grant.category === "DRUG"
            ? "drug_business_scope"
            : "device_business_scope"),
      )
      ?.items.filter((i) => i.active) || [],
);
const chosen = computed(() =>
  certs.value.find((c) => c.id === props.grant.certificate_id),
);
function partyChanged(id: string) {
  props.grant.party_id = id;
  props.grant.certificate_id = "";
  props.grant.certificate_version = "";
}
function certificateChanged(event: Event) {
  props.grant.certificate_id = (event.target as HTMLSelectElement).value;
  props.grant.certificate_version = chosen.value?.version || "";
}
</script>

<template>
  <div class="admission-grant">
    <div class="admission-fields">
      <PartyLookup
        v-if="party"
        :model-value="grant.party_id"
        :entity="entity"
        :channel="channel === 'PURCHASE' ? 'purchase' : 'sale'"
        label="往来单位"
        :qualified-only="false"
        :disabled="disabled"
        required
        @update:model-value="partyChanged"
      />
      <label
        >商品类别<select
          v-model="grant.category"
          :disabled="disabled"
          @change="grant.value = ''"
        >
          <option value="DEVICE">医疗器械</option>
          <option value="DRUG">药品</option>
        </select></label
      >
      <label
        >经营范围类别<select
          v-model="grant.value"
          :disabled="disabled"
          required
        >
          <option value="" disabled>选择已启用的范围字典</option>
          <option
            v-if="grant.value && !options.some((i) => i.value === grant.value)"
            :value="grant.value"
          >
            {{ grant.value }}（历史值）
          </option>
          <option v-for="item in options" :key="item.value" :value="item.value">
            {{ item.value }}
          </option>
        </select></label
      >
      <label
        >核验起始日期<DateInput
          v-model="grant.valid_from"
          label="核验起始日期"
          required
          :disabled="disabled"
      /></label>
      <label
        >核验截止日期<DateInput
          v-model="grant.valid_until"
          label="核验截止日期"
          required
          :disabled="disabled"
      /></label>
      <label
        >核验凭据编号<input
          v-model="grant.evidence_ref"
          required
          maxlength="200"
          :disabled="disabled"
      /></label>
      <label v-if="party"
        >关联证照<select
          :value="grant.certificate_id"
          required
          :disabled="disabled || loading"
          @change="certificateChanged"
        >
          <option value="" disabled>
            {{ loading ? "正在加载证照" : "请选择证照" }}
          </option>
          <option v-for="c in certs" :key="c.id" :value="c.id">
            {{ partyCertificateLabels[c.certificate_type] }} · {{ c.number }} ·
            v{{ c.version }} · {{ partyCertificateLabels[c.state] || c.state }}
          </option>
        </select></label
      >
      <button v-if="!disabled" type="button" @click="emit('remove')">
        移除此范围
      </button>
      <button
        v-if="party && grant.party_id && !disabled"
        type="button"
        :disabled="loading"
        @click="loadCertificates"
      >
        刷新证照
      </button>
    </div>
    <p v-if="party && chosen" class="admission-evidence">
      固定关联版本 v{{ grant.certificate_version }} · 当前版本 v{{
        chosen.version
      }}
      ·
      {{
        chosen.validity_mode === "FIXED"
          ? `${chosen.valid_from} 至 ${chosen.valid_until}`
          : partyCertificateLabels[chosen.validity_mode]
      }}<br />
      原件范围：{{ chosen.scope_text || "未填写；请核对原件后手工选择范围" }}
      <AttachmentLink
        v-if="chosen.attachment_id && grant.party_id"
        area="parties"
        :parent="grant.party_id"
        :id="chosen.attachment_id"
      />
    </p>
    <p
      v-if="chosen && chosen.version !== grant.certificate_version"
      class="error"
    >
      证照已变更。核对新原件后更新关联版本，保存并提交独立复核。
      <button
        v-if="!disabled"
        type="button"
        @click="grant.certificate_version = chosen.version"
      >
        已核对，关联当前证照版本
      </button>
    </p>
  </div>
</template>
