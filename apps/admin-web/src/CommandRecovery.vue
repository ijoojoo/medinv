<script setup lang="ts">
import { ref, watch } from "vue";
import { session } from "./api";
import {
  acknowledgeSubmission,
  recoveryBusy,
  recoveryStorageError,
  resumeSubmission,
  submissionArea,
  unresolved,
} from "./commandRecovery";
const emit = defineEmits<{ resolved: []; failure: [error: unknown] }>();
const confirming = ref(false),
  checked = ref(false),
  confirmed = ref("");
watch(
  () =>
    `${session.value?.principal_id}:${session.value?.tenant_id}:${session.value?.context_generation}`,
  () => {
    confirming.value = false;
    checked.value = false;
    confirmed.value = "";
  },
);
async function resume() {
  const context = `${session.value?.principal_id}:${session.value?.tenant_id}:${session.value?.context_generation}`;
  try {
    const { result } = await resumeSubmission();
    if (
      context !==
      `${session.value?.principal_id}:${session.value?.tenant_id}:${session.value?.context_generation}`
    )
      return;
    const number =
      result &&
      typeof result === "object" &&
      "number" in result &&
      typeof result.number === "string"
        ? result.number
        : "";
    confirmed.value = number
      ? `原提交已确认，单号：${number}。请从业务列表或待办继续办理。`
      : "原提交已确认。请从业务列表或待办核对最新状态后继续办理。";
    emit("resolved");
  } catch (error) {
    if (
      context ===
      `${session.value?.principal_id}:${session.value?.tenant_id}:${session.value?.context_generation}`
    )
      emit("failure", error);
  }
}
function acknowledge() {
  if (!checked.value || recoveryBusy.value) return;
  try {
    acknowledgeSubmission();
    confirming.value = false;
    emit("resolved");
  } catch (error) {
    emit("failure", error);
  }
}
</script>
<template>
  <section
    v-if="unresolved || recoveryStorageError"
    class="panel command-recovery"
    aria-label="待确认提交"
  >
    <h3>{{ recoveryBusy ? "正在提交，请稍候" : "上次提交的结果待确认" }}</h3>
    <p v-if="unresolved">
      {{ submissionArea(unresolved.path) }} ·
      {{ new Date(unresolved.created).toLocaleString("zh-CN") }}
    </p>
    <p v-if="recoveryStorageError" role="alert">{{ recoveryStorageError }}</p>
    <p v-else>
      继续原提交会使用原内容确认结果；已保存的操作不会重复办理。确认前请勿再次收付款或重新制单。
    </p>
    <div class="business-toolbar">
      <el-button
        v-if="unresolved"
        type="primary"
        :disabled="recoveryBusy"
        @click="resume"
        >继续原提交</el-button
      >
      <el-button
        :disabled="recoveryBusy"
        @click="
          confirming = true;
          checked = false;
        "
        >已人工核对，结束确认</el-button
      >
    </div>
  </section>
  <el-alert
    v-if="confirmed"
    :title="confirmed"
    type="success"
    show-icon
    @close="confirmed = ''"
  />
  <el-dialog
    v-model="confirming"
    title="确认已核对原业务"
    width="520px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <p>
      请先核对原单是否已保存、库存与款项是否已登记。结束确认只清除本标签页的恢复记录，不取消或撤销任何业务。
    </p>
    <label class="recovery-check">
      <input v-model="checked" type="checkbox" />
      我已核对原单及实际收付款，确认可以继续办理
    </label>
    <template #footer
      ><el-button @click="confirming = false">返回核对</el-button
      ><el-button
        type="primary"
        :disabled="!checked || recoveryBusy"
        @click="acknowledge"
        >结束本次确认</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.command-recovery {
  border-left: 4px solid #d49b31;
  margin-bottom: 20px;
}
.command-recovery h3 {
  margin-top: 0;
}
.command-recovery p {
  line-height: 1.7;
}
.recovery-check {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.6;
}
</style>
