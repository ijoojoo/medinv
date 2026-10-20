<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, quantity, useCommand } from "./business";
import { localTime } from "./sales";
import {
  dispositionKinds,
  qualityStates,
  stockQuality,
  type DispositionDetailData,
} from "./quality";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ failure: [error: unknown]; back: [] }>();
const data = ref<DispositionDetailData | null>(null),
  loading = ref(false);
const mode = ref(""),
  reason = ref(""),
  decision = ref("APPROVE");
const { busy, execute } = useCommand((e) => emit("failure", e));
const independent = computed(
  () => data.value?.document.created_by !== session.value?.principal_id,
);
const titles: Record<string, string> = {
  review: "独立审核处置",
  post: "确认处置过账",
  cancel: "取消处置",
};
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<DispositionDetailData>(
      `/api/v1/quality-dispositions/${props.id}`,
    );
    if (current === epoch) data.value = result;
  } catch (e) {
    if (current === epoch) emit("failure", e);
  } finally {
    if (current === epoch) loading.value = false;
  }
}
function begin(action: string) {
  mode.value = action;
  reason.value = "";
  decision.value = "APPROVE";
}
async function submit() {
  if (!data.value) return;
  await execute(
    `/api/v1/quality-dispositions/${props.id}/${mode.value}`,
    {
      expected_version: data.value.document.version,
      reason: reason.value,
      ...(mode.value === "review" ? { decision: decision.value } : {}),
    },
    async () => {
      mode.value = "";
      await load();
    },
  );
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!mode.value, busy: busy.value }),
    "quality",
  ),
);
</script>
<template>
  <div class="business-toolbar">
    <el-button :disabled="busy" @click="emit('back')">返回处置列表</el-button>
    <el-button :disabled="busy || loading" @click="load"
      >刷新处置详情</el-button
    >
  </div>
  <section v-if="data" class="panel" v-loading="loading">
    <div class="business-toolbar">
      <h3 class="mono">{{ data.document.number }}</h3>
      <el-tag>{{ qualityStates[data.document.state] }}</el-tag>
    </div>
    <dl class="facts">
      <dt>处置方式</dt>
      <dd>{{ dispositionKinds[data.document.kind] }}</dd>
      <dt>来源质量</dt>
      <dd>{{ stockQuality[data.document.source_quality] }}</dd>
      <dt>商品</dt>
      <dd>{{ data.document.name }}</dd>
      <dt>批号</dt>
      <dd>{{ data.document.lot_no }}</dd>
      <dt>仓库 / 库位</dt>
      <dd>
        {{ data.document.warehouse_name }} / {{ data.document.location_code }}
      </dd>
      <dt>处置数量</dt>
      <dd>{{ quantity(data.document.qty) }} {{ data.document.base_unit }}</dd>
      <dt>申请原因</dt>
      <dd>{{ data.document.reason }}</dd>
      <dt>复验 / 处置依据</dt>
      <dd>{{ data.document.evidence }}</dd>
      <dt>申请时间</dt>
      <dd>{{ localTime(data.document.created_at) }}</dd>
      <dt>审核时间</dt>
      <dd>
        {{
          data.document.reviewed_at
            ? localTime(data.document.reviewed_at)
            : "--"
        }}
      </dd>
      <template v-if="data.document.review_note"
        ><dt>审核意见</dt>
        <dd>{{ data.document.review_note }}</dd></template
      >
      <template v-if="data.document.cancel_note"
        ><dt>取消原因</dt>
        <dd>{{ data.document.cancel_note }}</dd></template
      >
    </dl>
    <p
      class="form-note"
      v-if="
        data.document.state === 'DRAFT' || data.document.state === 'APPROVED'
      "
    >
      申请和审核不占用库存。过账将重新核对剩余数量、质量和盘点冻结；制单人不能审核或过账本单。
    </p>
    <div class="business-toolbar" v-if="!loading">
      <el-button
        v-if="
          data.document.state === 'DRAFT' &&
          independent &&
          can('quality.review')
        "
        :disabled="busy"
        type="primary"
        @click="begin('review')"
        >独立审核处置</el-button
      >
      <el-button
        v-if="
          data.document.state === 'APPROVED' &&
          independent &&
          can('quality.post')
        "
        :disabled="busy"
        type="primary"
        @click="begin('post')"
        >确认处置过账</el-button
      >
      <el-button
        v-if="
          ['DRAFT', 'APPROVED'].includes(data.document.state) &&
          can('quality.write')
        "
        :disabled="busy"
        @click="begin('cancel')"
        >取消处置</el-button
      >
    </div>
    <template v-if="data.result">
      <h4>过账结果</h4>
      <p v-if="data.document.kind === 'RELEASE'">
        已将 {{ quantity(data.result.qty) }}
        {{ data.document.base_unit }} 转入原库位合格库存，批次总数量与成本不变。
      </p>
      <p v-else>
        已报损 {{ quantity(data.result.qty) }}
        {{ data.document.base_unit }}，扣减批次成本
        {{ data.result.loss_cost }} 元。
      </p>
      <p class="form-note">
        凭证 {{ data.result.posting_id }} · 可在库存台账查看本次流水。
      </p>
    </template>
  </section>
  <el-dialog
    :model-value="!!mode"
    :title="titles[mode]"
    width="560px"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    @close="mode = ''"
  >
    <form class="business-form" @submit.prevent="submit">
      <label v-if="mode === 'review'"
        >审核结论<select v-model="decision" :disabled="busy">
          <option value="APPROVE">通过</option>
          <option value="REJECT">拒绝并取消</option>
        </select></label
      >
      <p v-if="mode === 'post'" class="form-note">
        {{
          data?.document.kind === "RELEASE"
            ? "确认复验合格后放行。系统不会解除商品或整批质量冻结。"
            : "确认报损实物已完成处置并留存记录。过账会扣减库存和成本，不能直接撤销。"
        }}
      </p>
      <label
        >操作说明<input
          v-model="reason"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <div class="business-toolbar">
        <el-button :disabled="busy" @click="mode = ''">返回</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >保存</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
