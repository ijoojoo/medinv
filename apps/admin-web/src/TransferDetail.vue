<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, positive, quantity, useCommand } from "./business";
import { localTime } from "./sales";
import { transferStates, type TransferDetailData } from "./transfers";
import TransferLossPanel from "./TransferLossPanel.vue";
const lossBusy = ref(false);
const props = defineProps<{ id: string; initialLossId?: string }>();
const emit = defineEmits<{ failure: [error: unknown]; back: [] }>();
const data = ref<TransferDetailData | null>(null),
  loading = ref(false),
  mode = ref(""),
  reason = ref(""),
  decision = ref("APPROVE"),
  offset = ref(0);
const readings = ref<
  {
    line_id: string;
    name: string;
    remaining: string;
    qty: string;
    lot_no: string;
    quality_status: string;
  }[]
>([]);
const { busy, execute } = useCommand((e) => emit("failure", e));
const independent = computed(
  () => data.value?.document.created_by !== session.value?.principal_id,
);
const sent = computed(() => !!data.value?.document.dispatch_posting_id);
const titles: Record<string, string> = {
  review: "独立审核调拨",
  dispatch: "确认调拨发出",
  receive: "登记本次收货",
  cancel: "取消调拨",
};
let epoch = 0,
  operation = "";
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const result = await request<TransferDetailData>(
      `/api/v1/transfers/${props.id}?offset=${offset.value}`,
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
  operation = crypto.randomUUID();
  readings.value = (data.value?.lines || [])
    .filter((r) => positive(r.remaining_qty))
    .map((r) => ({
      line_id: r.id,
      name: r.name,
      remaining: r.remaining_qty,
      qty: "",
      lot_no: "",
      quality_status: "QUALIFIED",
    }));
}
async function submit() {
  if (!data.value) return;
  const body = {
    expected_version: data.value.document.version,
    reason: reason.value,
    ...(mode.value === "review" ? { decision: decision.value } : {}),
    ...(mode.value === "receive"
      ? {
          client_operation_id: operation,
          lines: readings.value
            .filter((r) => r.qty.trim())
            .map((r) => ({
              line_id: r.line_id,
              qty: r.qty,
              lot_no: r.lot_no,
              quality_status: r.quality_status,
            })),
        }
      : {}),
  };
  await execute(
    `/api/v1/transfers/${props.id}/${mode.value}`,
    body,
    async () => {
      mode.value = "";
      offset.value = 0;
      await load();
    },
  );
}
onMounted(load);
disposeGuard(
  registerCatalogChanges(
    () => ({ dirty: !!mode.value, busy: busy.value }),
    "transfers",
  ),
);
</script>
<template>
  <div class="business-toolbar">
    <el-button :disabled="busy || lossBusy" @click="emit('back')"
      >返回调拨列表</el-button
    ><el-button :disabled="busy || lossBusy || loading" @click="load"
      >刷新调拨详情</el-button
    >
  </div>
  <section v-if="data" class="panel" v-loading="loading">
    <div class="business-toolbar">
      <h3 class="mono">{{ data.document.number }}</h3>
      <el-tag>{{ transferStates[data.document.state] }}</el-tag>
    </div>
    <dl class="facts">
      <dt>调拨路线</dt>
      <dd>
        {{ data.document.source_name }} → {{ data.document.destination_name }} /
        {{ data.document.destination_location }}
      </dd>
      <dt>申请时间</dt>
      <dd>{{ localTime(data.document.created_at) }}</dd>
      <dt>调拨说明</dt>
      <dd>{{ data.document.reason }}</dd>
      <dt v-if="data.document.review_note">审核记录</dt>
      <dd v-if="data.document.review_note">{{ data.document.review_note }}</dd>
      <dt v-if="data.document.cancel_note">取消原因</dt>
      <dd v-if="data.document.cancel_note">{{ data.document.cancel_note }}</dd>
    </dl>
    <p class="form-note">
      申请和审核不预占库存。发出量 = 已收量 + 已核销损失 +
      剩余在途；仅在途归零才结清。正常调拨保持成本不变，损失核销同步扣减成本；均不产生应收应付。
    </p>
    <el-table :data="data.lines" data-testid="transfer-lines">
      <el-table-column label="商品 / 批号 / 调出库位" min-width="250"
        ><template #default="{ row }"
          ><b>{{ row.name }}</b
          ><small class="cell-sub"
            >{{ row.lot_no }} · {{ row.source_location }}</small
          ></template
        ></el-table-column
      >
      <el-table-column label="申请数量" width="140"
        ><template #default="{ row }"
          >{{ quantity(row.qty) }} {{ row.base_unit }}</template
        ></el-table-column
      >
      <el-table-column label="已发出" width="110"
        ><template #default="{ row }">{{
          sent ? quantity(row.qty) : "0"
        }}</template></el-table-column
      >
      <el-table-column label="累计已收" width="110"
        ><template #default="{ row }">{{
          quantity(row.received_qty)
        }}</template></el-table-column
      >
      <el-table-column label="已核销损失" width="120"
        ><template #default="{ row }">{{
          quantity(row.lost_qty)
        }}</template></el-table-column
      >
      <el-table-column label="仍在途" width="110"
        ><template #default="{ row }">{{
          sent ? quantity(row.remaining_qty) : "0"
        }}</template></el-table-column
      >
    </el-table>
    <div class="business-toolbar">
      <el-button
        v-if="
          data.document.state === 'DRAFT' &&
          independent &&
          can('transfers.review')
        "
        type="primary"
        :disabled="busy || lossBusy || loading"
        @click="begin('review')"
        >独立审核调拨</el-button
      >
      <el-button
        v-if="data.document.state === 'APPROVED' && can('transfers.dispatch')"
        type="primary"
        :disabled="busy || lossBusy || loading"
        @click="begin('dispatch')"
        >确认调拨发出</el-button
      >
      <el-button
        v-if="
          ['IN_TRANSIT', 'PART_RECEIVED'].includes(data.document.state) &&
          can('transfers.receive')
        "
        type="primary"
        :disabled="busy || lossBusy || loading"
        @click="begin('receive')"
        >登记本次收货</el-button
      >
      <el-button
        v-if="
          ['DRAFT', 'APPROVED'].includes(data.document.state) &&
          can('transfers.write')
        "
        :disabled="busy || lossBusy || loading"
        @click="begin('cancel')"
        >取消调拨</el-button
      >
    </div>
    <p v-if="data.document.state === 'DRAFT' && !independent" class="form-note">
      请由其他有审核权限的成员独立审核本单。
    </p>
    <TransferLossPanel
      v-if="sent"
      :transfer="data.document"
      :initial-id="props.initialLossId"
      :lines="data.lines"
      :disabled="busy || loading"
      @failure="emit('failure', $event)"
      @changed="load"
      @busy="lossBusy = $event"
    />
    <h3>分次收货记录</h3>
    <el-table :data="data.receipts" empty-text="尚未登记收货">
      <el-table-column prop="number" label="收货单号" min-width="265" />
      <el-table-column label="收货时间" min-width="170"
        ><template #default="{ row }">{{
          localTime(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column label="本次数量 / 质量" min-width="200"
        ><template #default="{ row }"
          ><div v-for="l in row.lines" :key="l.line_id">
            {{ data.lines.find((r) => r.id === l.line_id)?.name }} ·
            {{ quantity(l.qty) }} ·
            {{ l.quality_status === "QUALIFIED" ? "合格" : "隔离待验" }}
          </div></template
        ></el-table-column
      >
      <el-table-column prop="note" label="收货说明" min-width="170" />
    </el-table>
    <div class="pager">
      <el-button
        :disabled="!offset || loading"
        @click="
          offset -= 50;
          load();
        "
        >上一页收货</el-button
      ><el-button
        :disabled="!data.has_more || loading"
        @click="
          offset += 50;
          load();
        "
        >下一页收货</el-button
      >
    </div>
  </section>
  <el-dialog
    :model-value="!!mode"
    :title="titles[mode]"
    :width="mode === 'receive' ? '800px' : '560px'"
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
      <p v-if="mode === 'dispatch'" class="form-note">
        核对实物后确认发出全部申请数量。系统从调出仓扣减并转入本单在途，目的仓须另行登记实际收货。
      </p>
      <template v-if="mode === 'receive'">
        <p class="form-note">
          只填写本次实际收到的数量和批号，未到货行留空。异常商品请选隔离待验；隔离库存不可销售，后续须另行质量处置。
        </p>
        <div v-for="(r, i) in readings" :key="r.line_id" class="business-form">
          <h4>
            {{ i + 1 }}. {{ r.name }} · 剩余在途 {{ quantity(r.remaining) }}
          </h4>
          <div class="form-grid">
            <label
              >本次收货数量<input
                v-model="r.qty"
                :aria-label="'本次收货数量 ' + (i + 1)"
                inputmode="decimal"
                maxlength="22"
                :disabled="busy"
            /></label>
            <label
              >实物批号<input
                v-model="r.lot_no"
                :aria-label="'实物批号 ' + (i + 1)"
                :required="!!r.qty.trim()"
                maxlength="100"
                :disabled="busy"
            /></label>
            <label
              >收货质量<select
                v-model="r.quality_status"
                :aria-label="'收货质量 ' + (i + 1)"
                :disabled="busy"
              >
                <option value="QUALIFIED">合格</option>
                <option value="QUARANTINE">隔离待验</option>
              </select></label
            >
          </div>
        </div>
      </template>
      <label
        >操作说明<input
          v-model="reason"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <div class="business-toolbar">
        <el-button :disabled="busy" @click="mode = ''">返回</el-button
        ><el-button
          native-type="submit"
          type="primary"
          :loading="busy"
          :disabled="mode === 'receive' && !readings.some((r) => r.qty.trim())"
          >保存</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
