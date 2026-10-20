<script setup lang="ts">
import { registerCatalogChanges } from "./catalogChanges";
import { onUnmounted as disposeGuard } from "vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request, session } from "./api";
import { can, quantity, useCommand } from "./business";
import { localTime } from "./sales";
import { countStates, type CountDetailData } from "./counts";
const props = defineProps<{ id: string }>();
const emit = defineEmits<{ failure: [error: unknown]; back: [] }>();
const data = ref<CountDetailData | null>(null),
  loading = ref(false),
  mode = ref(""),
  reason = ref(""),
  decision = ref("APPROVE");
const readings = ref<
  {
    line_id: string;
    name: string;
    actual_qty: string;
    lot_no: string;
    gain_unit_cost: string;
    pool_qty: string;
    note: string;
  }[]
>([]);
const { busy, execute } = useCommand((e) => emit("failure", e));
const independent = computed(
  () =>
    !!data.value &&
    ![
      data.value.document.created_by,
      data.value.document.submitted_by,
    ].includes(session.value?.principal_id || ""),
);
const titles: Record<string, string> = {
  submit: "录入实盘并提交",
  review: "独立审核盘点",
  post: "确认盘点过账",
  cancel: "取消盘点",
};
let epoch = 0;
onUnmounted(() => epoch++);
async function load() {
  const current = ++epoch;
  loading.value = true;
  try {
    const value = await request<CountDetailData>(
      "/api/v1/stock-counts/" + props.id,
    );
    if (current === epoch) data.value = value;
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
  readings.value = (data.value?.lines || []).map((r) => ({
    line_id: r.id,
    name: r.name,
    actual_qty: "",
    lot_no: "",
    gain_unit_cost: "",
    pool_qty: r.pool_qty,
    note: "",
  }));
}
async function submit() {
  if (!data.value) return;
  const body = {
    expected_version: data.value.document.version,
    reason: reason.value,
    ...(mode.value === "review" ? { decision: decision.value } : {}),
    ...(mode.value === "submit"
      ? {
          lines: readings.value.map((r) => ({
            line_id: r.line_id,
            actual_qty: r.actual_qty,
            lot_no: r.lot_no,
            note: r.note,
            gain_unit_cost: r.gain_unit_cost || null,
          })),
        }
      : {}),
  };
  await execute(
    "/api/v1/stock-counts/" + props.id + "/" + mode.value,
    body,
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
    "counts",
  ),
);
</script>
<template>
  <div class="business-toolbar">
    <el-button @click="emit('back')">返回盘点列表</el-button
    ><el-button :disabled="busy" @click="load">刷新盘点详情</el-button>
  </div>
  <section v-if="data" class="panel" v-loading="loading">
    <div class="business-toolbar">
      <h3 class="mono">{{ data.document.number }}</h3>
      <el-tag>{{ countStates[data.document.state] }}</el-tag>
    </div>
    <dl class="facts">
      <dt>仓库 / 快照时间</dt>
      <dd>
        {{ data.document.warehouse_name }} ·
        {{ localTime(data.document.created_at) }}
      </dd>
      <dt>盘点说明</dt>
      <dd>{{ data.document.reason }}</dd>
      <dt v-if="data.document.submit_note">实盘说明</dt>
      <dd v-if="data.document.submit_note">{{ data.document.submit_note }}</dd>
      <dt v-if="data.document.review_note">审核记录</dt>
      <dd v-if="data.document.review_note">{{ data.document.review_note }}</dd>
      <dt v-if="data.document.cancel_note">取消原因</dt>
      <dd v-if="data.document.cancel_note">{{ data.document.cancel_note }}</dd>
    </dl>
    <p
      v-if="['COUNTING', 'SUBMITTED', 'APPROVED'].includes(data.document.state)"
      class="form-note"
    >
      盘点冻结中：所选主体批次（含其他库位、仓库）暂不可出入库或预占。请暂停实物移动；完成过账或取消盘点后自动解除。
    </p>
    <p class="form-note">
      盘盈盘亏仅调整本次选定范围。盘亏按原批次加权成本计价；有成本数量的盘盈沿用原均价，零成本数量批次须核定单位成本。过账不产生供应商应付或客户应收。
    </p>
    <el-table :data="data.lines">
      <el-table-column label="商品 / 批号 / 库位" min-width="220"
        ><template #default="{ row }"
          ><b>{{ row.name }}</b
          ><small class="cell-sub"
            >{{ row.lot_no }} · {{ row.location_code }}</small
          ></template
        ></el-table-column
      >
      <el-table-column label="账面数量" width="125"
        ><template #default="{ row }"
          >{{ quantity(row.book_qty) }} {{ row.base_unit
          }}<small class="cell-sub"
            >预占 {{ quantity(row.reserved_qty) }} / 冻结
            {{ quantity(row.blocked_qty) }}</small
          ></template
        ></el-table-column
      >
      <el-table-column label="实盘 / 差异" width="130"
        ><template #default="{ row }"
          >{{ row.actual_qty === null ? "待录入" : quantity(row.actual_qty)
          }}<small v-if="row.qty_delta !== null" class="cell-sub"
            >差异 {{ quantity(row.qty_delta) }}</small
          ></template
        ></el-table-column
      >
      <el-table-column
        :label="
          data.document.state === 'POSTED'
            ? '成本变动（元）'
            : '拟调整成本（元）'
        "
        width="160"
        ><template #default="{ row }"
          >{{ row.cost_delta === null ? "—" : quantity(row.cost_delta)
          }}<small v-if="row.gain_unit_cost" class="cell-sub"
            >核定单价 {{ quantity(row.gain_unit_cost) }}</small
          ></template
        ></el-table-column
      >
      <el-table-column
        prop="count_note"
        label="实盘及差异依据"
        min-width="180"
      />
    </el-table>
    <dl v-if="data.result" class="facts" data-testid="count-result">
      <dt>盘盈成本</dt>
      <dd>{{ quantity(data.result.gain_cost) }} 元</dd>
      <dt>盘亏成本</dt>
      <dd>{{ quantity(data.result.loss_cost) }} 元</dd>
      <dt>成本净变动</dt>
      <dd>{{ quantity(data.result.cost_delta) }} 元</dd>
      <dt>差异行数</dt>
      <dd>{{ data.result.changed_lines }}</dd>
      <dt>过账编号</dt>
      <dd class="mono">{{ data.result.posting_id }}</dd>
    </dl>
    <div class="business-toolbar">
      <el-button
        v-if="data.document.state === 'COUNTING' && can('counts.write')"
        type="primary"
        @click="begin('submit')"
        >录入实盘并提交</el-button
      >
      <el-button
        v-if="
          data.document.state === 'SUBMITTED' &&
          can('counts.review') &&
          independent
        "
        type="primary"
        @click="begin('review')"
        >独立审核盘点</el-button
      >
      <el-button
        v-if="
          data.document.state === 'APPROVED' &&
          can('counts.post') &&
          independent
        "
        type="primary"
        @click="begin('post')"
        >确认盘点过账</el-button
      >
      <el-button
        v-if="
          ['COUNTING', 'SUBMITTED', 'APPROVED'].includes(data.document.state) &&
          can('counts.write')
        "
        @click="begin('cancel')"
        >取消盘点</el-button
      >
    </div>
    <p
      v-if="
        ['SUBMITTED', 'APPROVED'].includes(data.document.state) && !independent
      "
      class="form-note"
    >
      请由制单人和实盘录入人之外的成员独立审核并过账。
    </p>
  </section>
  <el-dialog
    :model-value="!!mode"
    :title="titles[mode]"
    :width="mode === 'submit' ? '900px' : '560px'"
    class="business-dialog"
    :show-close="!busy"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    @close="mode = ''"
  >
    <form class="business-form" @submit.prevent="submit">
      <template v-if="mode === 'submit'">
        <p class="form-note">
          请按实物逐行录入数量和批号，数量为零也须明确填写。提交后实盘记录不可修改；错录请取消后重新盘点。
        </p>
        <div
          v-for="(r, index) in readings"
          :key="r.line_id"
          class="count-reading"
        >
          <h4>{{ index + 1 }}. {{ r.name }}</h4>
          <div class="form-grid">
            <label
              >实盘数量<input
                v-model="r.actual_qty"
                :aria-label="'实盘数量 ' + (index + 1)"
                required
                inputmode="decimal"
                maxlength="22" /></label
            ><label
              >实物批号<input
                v-model="r.lot_no"
                :aria-label="'实物批号 ' + (index + 1)"
                required
                maxlength="100"
            /></label>
          </div>
          <label v-if="!/[1-9]/.test(r.pool_qty)"
            >零库存盘盈核定单位成本（元）<input
              v-model="r.gain_unit_cost"
              :aria-label="'核定单位成本 ' + (index + 1)"
              inputmode="decimal"
              maxlength="26"
              placeholder="仅实际发生盘盈时必填"
          /></label>
          <label
            >实盘及差异依据<input
              v-model="r.note"
              :aria-label="'实盘依据 ' + (index + 1)"
              required
              maxlength="200"
              placeholder="无差异也请注明；核定成本时说明计价依据"
          /></label>
        </div>
      </template>
      <label v-if="mode === 'review'"
        >审核结论<select v-model="decision">
          <option value="APPROVE">通过</option>
          <option value="REJECT">拒绝并取消</option>
        </select></label
      >
      <p v-if="mode === 'post'" class="form-note">
        确认实盘和差异依据后，将同时更新库存、成本及台账。无差异单只保留盘点完成记录；已过账盘点不能直接撤销。
      </p>
      <label>操作说明<input v-model="reason" required maxlength="200" /></label>
      <div class="business-toolbar">
        <el-button :disabled="busy" @click="mode = ''">返回</el-button
        ><el-button native-type="submit" type="primary" :loading="busy"
          >保存</el-button
        >
      </div>
    </form>
  </el-dialog>
</template>
<style scoped>
.count-reading {
  padding: 14px 0;
  border-bottom: 1px solid var(--line, #e2e8e5);
}
.count-reading h4 {
  margin: 0 0 12px;
}
.count-reading label {
  display: grid;
  gap: 8px;
  margin: 10px 0;
}
</style>
