<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { can, useCommand } from "./business";
import { channelNames, subjectNames, certificateTypes } from "./qualification";
import { certificateLabels } from "./productCertificates";
const props = defineProps<{
  kind: "product" | "party";
  id: string;
  version: string | number;
  readonly?: boolean;
}>();
const emit = defineEmits<{ failure: [error: unknown]; saved: [] }>();
interface Bundle {
  version: string;
  has_changes: boolean;
  draft: {
    payload: Record<string, any>;
    before_snapshot: Record<string, any>;
  } | null;
}
const data = ref<Bundle>(),
  reason = ref(""),
  opened = ref(false);
const certificateNumbers = ref<Record<string, string>>({});
const { busy, execute } = useCommand((e) => emit("failure", e));
const names: Record<string, string> = {
  name: "名称",
  approval_number: "批准 / 注册 / 备案编号",
  valid_from: "生效日期",
  valid_until: "截止日期",
  license_no: "原资质编号",
  categories: "经营商品大类",
  kind: "供销身份",
  device_class: "器械管理类别",
  device_classification_code: "器械分类编码",
  device_classification_version: "分类目录版本",
  regulatory_subject: "监管主体",
  product_certificates: "产品证件",
  specification: "规格",
  manufacturer_name: "生产企业",
  manufacturer: "生产企业",
  scopes: "经营范围",
  record: "主体资料",
  master: "商品资料",
  certificates: "证件资料",
  category: "商品类别",
  codes: "允许类目",
  excluded_codes: "排除类目",
  directory_version: "目录版本",
  include_children: "覆盖下级分类",
  certificate_ids: "关联证件",
  exceptions: "适用例外",
  rule_id: "对应规则",
  reason: "说明",
  subject_type: "单位身份",
  channels: "业务",
  enabled: "启用",
  note: "说明",
};
const rows = computed(() => {
  const bundle = data.value?.draft;
  if (!bundle) return [];
  const result: {
    field: string;
    key?: string;
    before: unknown;
    after: unknown;
  }[] = [];
  for (const [area, payload] of Object.entries(bundle.payload)) {
    if (area === "record" || area === "master") {
      const after = area === "master" ? payload.values : payload;
      const before =
        area === "master"
          ? bundle.before_snapshot.master?.values || {}
          : bundle.before_snapshot.record || {};
      for (const [key, value] of Object.entries(after))
        if (JSON.stringify(value) !== JSON.stringify(before[key]))
          result.push({
            field: names[key] || key,
            key,
            before: before[key],
            after: value,
          });
    } else if (area === "scopes") {
      const previous = bundle.before_snapshot.scopes?.scopes || [];
      for (const scope of payload.scopes) {
        const old = previous.find((s: any) => s.id === scope.id) || {};
        for (const [key, value] of Object.entries(scope))
          if (
            key !== "id" &&
            JSON.stringify(value) !== JSON.stringify(old[key])
          )
            result.push({
              field: `${scope.name} · ${names[key] || key}`,
              key,
              before: old[key],
              after: value,
            });
      }
      for (const scope of previous)
        if (!payload.scopes.some((s: any) => s.id === scope.id))
          result.push({
            field: `经营范围 · ${scope.name}`,
            before: scope,
            after: "已移除",
          });
    } else if (area === "certificates") {
      for (const [id, entry] of Object.entries(payload) as [string, any][])
        result.push({
          field: `证件 ${entry.values.number}`,
          before: bundle.before_snapshot.certificates?.[id],
          after: { ...entry.values, state: entry.state || "随主体审核" },
        });
    } else
      result.push({
        field: names[area] || area,
        before: bundle.before_snapshot[area],
        after: payload,
      });
  }
  return result;
});
function display(value: unknown, key = ""): string {
  if (value === null || value === undefined || value === "") return "未填写";
  if (Array.isArray(value))
    return value.map((item) => display(item, key)).join("；") || "无";
  if (typeof value === "object")
    return Object.entries(value)
      .filter(
        ([key]) =>
          ![
            "tenant_id",
            "created_by",
            "updated_by",
            "revision_editors",
            "created_at",
            "updated_at",
            "reviewed_by",
            "reviewed_at",
            "product_id",
            "party_id",
            "id",
          ].includes(key),
      )
      .map(
        ([key, val]) =>
          `${names[key] || ({ number: "编号", certificate_type: "证件类型", validity_mode: "有效期方式", evidence_ref: "核验凭据", holder_name: "证件主体", issuer: "发证机关", state: "状态" } as Record<string, string>)[key] || key}：${display(val, key)}`,
      )
      .join("\n");
  if (typeof value === "boolean") return value ? "是" : "否";
  if (["certificate_ids", "certificate_id"].includes(key))
    return (
      certificateNumbers.value[String(value)] ||
      "已关联证件（可在证照资料中查看）"
    );
  const labels: Record<string, Record<string, string>> = {
    category: { DEVICE: "医疗器械", DRUG: "药品" },
    device_class: { I: "第一类", II: "第二类", III: "第三类" },
    subject_type: subjectNames,
    channels: channelNames,
    certificate_type: { ...certificateLabels, ...certificateTypes },
    validity_mode: { ...certificateLabels, LONG_TERM: "原件载明长期" },
    state: certificateLabels,
  };
  return labels[key]?.[String(value)] || String(value);
}
let loadEpoch = 0;
async function load() {
  const epoch = ++loadEpoch;
  try {
    const result = await request<Bundle>(
      `/api/v1/catalog/changes/${props.kind}/${props.id}`,
    );
    if (epoch !== loadEpoch) return;
    if (result.has_changes) {
      const listing = await request<{
        items: { id: string; number: string }[];
      }>(
        `/api/v1/catalog/${props.kind === "product" ? "products" : "parties"}/${props.id}/certificates`,
      );
      if (epoch !== loadEpoch) return;
      certificateNumbers.value = Object.fromEntries(
        listing.items.map((c) => [c.id, c.number]),
      );
    }
    data.value = result;
  } catch (e) {
    if (epoch === loadEpoch) emit("failure", e);
  }
}
async function discard() {
  if (props.readonly) return;
  await execute(
    `/api/v1/catalog/changes/${props.kind}/${props.id}/discard`,
    { expected_version: data.value!.version, reason: reason.value },
    async () => {
      opened.value = false;
      reason.value = "";
      await load();
      emit("saved");
    },
  );
}
watch(() => [props.id, props.version], load, { immediate: true });
onUnmounted(() => loadEpoch++);
</script>
<template>
  <div v-if="data?.has_changes" class="master-change-summary">
    <div>
      <strong>待审核变更 · {{ rows.length }} 项</strong>
      <p>当前业务仍使用原有效资料。证件、经营范围与主体资料一次审核生效。</p>
    </div>
    <el-button @click="opened = true">查看变更对比</el-button>
    <el-dialog
      v-model="opened"
      title="主体资料变更对比"
      width="min(960px, 96vw)"
      :close-on-click-modal="false"
      ><div class="table-scroll">
        <table class="business-table change-table">
          <thead>
            <tr>
              <th>变更内容</th>
              <th>原有效资料</th>
              <th>待审核资料</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in rows" :key="i">
              <th>{{ r.field }}</th>
              <td>{{ display(r.before, r.key) }}</td>
              <td>{{ display(r.after, r.key) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="form-note">
        关闭后从主体审核入口一次审核全部变更。撤回只丢弃当前变更草稿，操作记录仍保留。
      </p>
      <div
        v-if="!readonly && can('catalog.write')"
        class="qualification-actions"
      >
        <input
          v-model="reason"
          placeholder="撤回变更原因"
          aria-label="撤回变更原因"
        /><el-button :disabled="!reason || busy" @click="discard"
          >撤回全部待审核变更</el-button
        >
      </div></el-dialog
    >
  </div>
</template>
<style scoped>
.master-change-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: space-between;
  padding: 12px 16px;
  margin: 12px 0;
  border: 1px solid var(--ui-border);
  background: var(--ui-tint);
  border-radius: 8px;
}
.master-change-summary p {
  margin: 5px 0 0;
  font-size: 12px;
}
.change-table {
  min-width: 700px;
  table-layout: fixed;
}
.change-table td {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  vertical-align: top;
}
.change-table th:first-child {
  width: 150px;
}
</style>
