<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { useCommand } from "./business";
import { localTime } from "./sales";
const props = defineProps<{
  id: string;
  version: string;
  business: string;
  dirty: boolean;
  disabled: boolean;
}>();
const emit = defineEmits<{ failure: [error: unknown] }>();
interface Policy {
  enabled: boolean;
  version: string;
  design_id?: string;
  design_version?: string;
  definition: {
    name: string;
    steps: { id: string; name: string; role: string; mode: string }[];
  } | null;
  history: {
    created_at: string;
    detail: { reason: string; enabled: boolean; version: string };
  }[];
}
const current = ref<Policy | null>(null),
  loading = ref(false),
  reason = ref("");
const { busy, execute } = useCommand((e) => emit("failure", e));
defineExpose({ busy, dirty: computed(() => !!reason.value.trim()) });
const roles: Record<string, string> = {
  QUALITY: "质量审核员",
  ADMIN: "企业管理员",
  OWNER: "企业所有者",
};
const selectedActive = computed(
  () => current.value?.enabled && current.value.design_id === props.id,
);
let epoch = 0;
async function load() {
  const token = ++epoch;
  loading.value = true;
  current.value = null;
  try {
    const result = await request<Policy>(
      `/api/v1/approval-policies/purchase?entity_id=${activeEntity.value}`,
    );
    if (token === epoch) current.value = result;
  } catch (e) {
    if (token === epoch) emit("failure", e);
  } finally {
    if (token === epoch) loading.value = false;
  }
}
async function publish(enabled: boolean) {
  if (!current.value || !reason.value.trim()) return;
  await execute(
    "/api/v1/approval-policies/purchase",
    {
      entity_id: activeEntity.value,
      expected_version: current.value.version,
      design_id: props.id,
      design_version: props.version,
      enabled,
      reason: reason.value,
    },
    async () => {
      reason.value = "";
      await load();
    },
  );
}
watch(
  () => [props.id, props.version, activeEntity.value],
  () => {
    reason.value = "";
    void load();
  },
);
onMounted(load);
onUnmounted(() => ++epoch);
</script>
<template>
  <section
    class="approval-publication"
    aria-label="采购审批发布"
    v-loading="loading"
  >
    <header>
      <h3>采购审批发布</h3>
      <el-button :disabled="busy" @click="load">刷新发布状态</el-button>
    </header>
    <p v-if="current?.enabled">
      本主体当前使用 <strong>{{ current.definition?.name }}</strong> · 发布版本
      {{ current.version }} · 设计版本 {{ current.design_version }}
    </p>
    <p v-else>
      当前新采购单沿用独立审核；发布后，新建或修改内容的采购单绑定所选流程。
    </p>
    <p
      v-if="selectedActive && current?.design_version !== version"
      class="changed"
    >
      本设计有未发布修改，已绑定单据继续按原版本办理。
    </p>
    <div class="publish-preview" v-if="current?.definition">
      <span v-for="(step, index) in current.definition.steps" :key="step.id"
        >{{ index + 1 }}. {{ step.name }}
        <small>{{ roles[step.role] }}</small></span
      >
    </div>
    <p class="form-note">
      已绑定流程的单据保留其版本。停用只影响后续新建/内容修订；退回后的重新提交沿用原绑定版本。当前支持顺序或签，会签和其他业务暂保留为设计稿。商品及往来单位保持主体一次审核。
    </p>
    <form class="business-form" @submit.prevent="publish(true)">
      <label
        >发布/停用说明<input
          v-model="reason"
          maxlength="200"
          required
          :disabled="busy || disabled"
      /></label>
      <div class="form-actions">
        <el-button
          v-if="business === 'PURCHASE'"
          type="primary"
          native-type="submit"
          :loading="busy"
          :disabled="!current || dirty || disabled || !reason.trim()"
          >发布并用于新采购单</el-button
        ><el-button
          v-if="selectedActive"
          :disabled="busy || disabled || !reason.trim()"
          @click="publish(false)"
          >停用后续绑定</el-button
        >
      </div>
      <small v-if="dirty">请先保存设计稿，再发布当前版本。</small>
    </form>
    <details v-if="current?.history.length">
      <summary>发布与停用记录</summary>
      <p v-for="(event, index) in current.history" :key="index">
        {{ localTime(event.created_at) }} · v{{ event.detail.version }} ·
        {{ event.detail.enabled ? "发布" : "停用" }} · {{ event.detail.reason }}
      </p>
    </details>
  </section>
</template>
<style scoped>
.approval-publication {
  max-width: 1100px;
  margin: 24px auto 0;
  border-top: 1px solid var(--ui-border);
  padding-top: 20px;
}
.approval-publication header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.approval-publication p {
  line-height: 1.8;
  font-size: 13px;
}
.publish-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 16px 0;
}
.publish-preview span {
  background: var(--ui-tint);
  border: 1px solid var(--ui-border);
  padding: 10px 14px;
  border-radius: 6px;
}
.publish-preview small {
  display: block;
  margin-top: 6px;
  color: var(--ui-muted);
}
.changed {
  color: #9a6414;
}
.approval-publication details {
  margin-top: 18px;
}
.approval-publication .business-form {
  display: grid;
  gap: 10px;
}
</style>
