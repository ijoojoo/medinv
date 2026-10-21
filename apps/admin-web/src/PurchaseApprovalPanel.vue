<script setup lang="ts">
import { computed } from "vue";
import { session } from "./api";
import { localTime } from "./sales";
import type { PurchaseApprovalFlow } from "./approvalWorkflow";
const props = defineProps<{ flow: PurchaseApprovalFlow }>();
const states: Record<string, string> = {
  PENDING: "审批中",
  RETURNED: "已退回 · 待修改或重新提交",
  APPROVED: "审批通过",
  CANCELLED: "已取消",
  SUPERSEDED: "内容修订后已重新发起",
};
const roles: Record<string, string> = {
  QUALITY: "质量审核员",
  ADMIN: "企业管理员",
  OWNER: "企业所有者",
};
const actions: Record<string, string> = {
  APPROVE: "通过节点",
  REJECT: "退回修改",
  RESUBMIT: "重新提交",
  CANCEL: "取消",
};
const rows = computed(() => [props.flow, ...(props.flow.previous || [])]);
function passed(index: number) {
  return props.flow.decisions.some(
    (d) =>
      d.round === props.flow.round &&
      d.step_index === index &&
      d.action === "APPROVE",
  );
}
</script>
<template>
  <section class="purchase-approval" aria-label="采购审批进度">
    <header>
      <strong>{{ flow.definition.name }}</strong
      ><span>{{ states[flow.state] }} · 第 {{ flow.round }} 轮</span>
    </header>
    <p>
      绑定发布版本 {{ flow.policy_version }} / 设计版本
      {{ flow.definition.design_version }} · 当前采购内容第
      {{ flow.revision_no }} 版
    </p>
    <ol class="approval-progress">
      <li
        v-for="(step, index) in flow.definition.steps"
        :key="step.id"
        :class="{
          passed: passed(index),
          current: flow.state === 'PENDING' && index === flow.step_index,
        }"
      >
        <b>{{ passed(index) ? "✓" : index + 1 }}</b>
        <div>
          <strong>{{ step.name }}</strong
          ><small>{{ roles[step.role] }} · 或签</small>
        </div>
        <span>{{
          passed(index)
            ? "已通过"
            : flow.state === "PENDING" && index === flow.step_index
              ? "当前节点"
              : flow.state === "RETURNED" && index === flow.step_index
                ? "已退回"
                : ["CANCELLED", "SUPERSEDED"].includes(flow.state)
                  ? "未办理"
                  : "待办理"
        }}</span>
      </li>
    </ol>
    <p v-if="flow.state === 'PENDING' && !flow.can_approve" class="form-note">
      请由当前节点指定角色的独立成员办理；制单人及本轮修改/重新提交人员不能审批。
    </p>
    <p v-if="flow.state === 'RETURNED'" class="form-note">
      可以修改采购内容后重新发起，或核对退回意见并填写说明后重新提交。重新提交将从首个节点办理。
    </p>
    <details
      v-for="(row, index) in rows"
      :key="row.id"
      :open="index === 0 && flow.state === 'RETURNED'"
    >
      <summary>
        {{
          index === 0
            ? "本版审批记录"
            : `采购内容第 ${row.revision_no} 版 · ${row.definition.name}`
        }}（{{ row.decisions.length }}）
      </summary>
      <p v-if="!row.decisions.length">尚无审批记录</p>
      <article v-for="(decision, n) in row.decisions" :key="n">
        <small
          >{{ localTime(decision.at) }} · 第 {{ decision.round }} 轮 ·
          {{ decision.actor === session?.principal_id ? "本人" : "其他成员"
          }}{{ decision.role ? " · " + roles[decision.role] : "" }}</small
        >
        <p>
          <strong>{{ actions[decision.action] }}</strong>
          {{
            decision.action === "APPROVE" || decision.action === "REJECT"
              ? row.definition.steps[decision.step_index]?.name
              : ""
          }}
          · {{ decision.reason }}
        </p>
      </article>
    </details>
  </section>
</template>
<style scoped>
.purchase-approval {
  margin: 18px 0;
  padding: 18px;
  background: var(--ui-tint);
  border: 1px solid var(--ui-border);
  border-radius: 8px;
}
.purchase-approval header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.purchase-approval p {
  font-size: 13px;
  line-height: 1.8;
}
.approval-progress {
  list-style: none;
  margin: 16px 0;
  padding: 0;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.approval-progress li {
  display: flex;
  gap: 10px;
  padding: 12px;
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  align-items: center;
  min-width: 220px;
}
.approval-progress li.current {
  border-color: var(--ui-accent);
}
.approval-progress b {
  border-radius: 50%;
  padding: 4px 8px;
  background: var(--ui-tint);
}
.approval-progress small {
  display: block;
  margin-top: 5px;
  color: var(--ui-muted);
}
.approval-progress li > span {
  font-size: 12px;
  margin-left: auto;
  color: var(--ui-muted);
}
.approval-progress .passed b {
  background: #e8f5ed;
  color: #246b40;
}
.purchase-approval details {
  border-top: 1px solid var(--ui-border);
  padding: 12px 0;
}
.purchase-approval article {
  padding: 10px 0;
}
.purchase-approval article small {
  color: var(--ui-muted);
}
.purchase-approval article p {
  margin: 5px 0;
}
</style>
