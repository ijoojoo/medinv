<script setup lang="ts">
import { computed, ref } from "vue";
import { activeEntity } from "./entityScope";
import { localTime } from "./sales";
import { goPage, showRecord } from "./workspace";
import {
  amount,
  can,
  cents,
  change,
  money,
  useWorkbenchData,
} from "./workbench";
import WorkbenchTrend from "./WorkbenchTrend.vue";
import WorkbenchStatus from "./WorkbenchStatus.vue";
const props = defineProps<{ active: boolean }>();
type Totals = {
  sales: string | null;
  net: string | null;
  profit: string | null;
  cash: string;
};
type Period = {
  as_of: string;
  start: string;
  end: string;
  days: number;
  current: Totals;
  previous: Totals;
  series: { day: string; sales: string | null; cash: string | null }[];
};
type Snapshot = {
  as_of: string;
  accounts: {
    receivable: string;
    payable: string;
    current: string;
    late: string;
    overdue: string;
    overdue_customers: number;
  };
  inventory: null | {
    groups: { category: string; amount: string; skus: number }[];
    amount: string;
    near_skus: number;
    near_amount: string;
    expired_skus: number;
    expired_amount: string;
  };
};
type Detail = {
  as_of: string;
  total: number;
  amount: string;
  has_more: boolean;
  items: {
    id: string;
    name: string;
    number: string;
    day: string;
    amount: string;
    order_id?: string;
    lot_no?: string;
    qty?: string;
    base_unit?: string;
  }[];
};
const days = ref(7),
  metric = ref<"sales" | "cash">("sales");
const {
  data: period,
  loading: periodLoading,
  error: periodError,
  load: loadPeriod,
} = useWorkbenchData<Period>(
  () =>
    `/api/v1/workbench/period?entity_id=${activeEntity.value}&days=${days.value}`,
  () => props.active,
);
const {
  data: snapshot,
  loading: snapshotLoading,
  error: snapshotError,
  load: loadSnapshot,
} = useWorkbenchData<Snapshot>(
  () => `/api/v1/workbench/snapshot?entity_id=${activeEntity.value}`,
  () => props.active,
);
const detailOpen = ref(false),
  detailKind = ref("overdue"),
  offset = ref(0);
const names: Record<string, string> = {
  overdue: "逾期应收明细",
  near: "未来90日内到期库存",
  expired: "已过期库存",
};
const {
  data: detail,
  loading: detailLoading,
  error: detailError,
  load: loadDetail,
} = useWorkbenchData<Detail>(
  () =>
    `/api/v1/workbench/attention?entity_id=${activeEntity.value}&kind=${detailKind.value}&offset=${offset.value}`,
  () => props.active && detailOpen.value,
);
function openDetail(kind: string) {
  detailKind.value = kind;
  offset.value = 0;
  detailOpen.value = true;
}
const overdue = computed(() =>
  snapshot.value
    ? amount(
        cents(snapshot.value.accounts.late) +
          cents(snapshot.value.accounts.overdue),
      )
    : undefined,
);
const margin = computed(() => {
  const totals = period.value?.current;
  if (
    !totals ||
    totals.profit === null ||
    totals.net === null ||
    cents(totals.net) <= 0n
  )
    return "—";
  return amount((cents(totals.profit) * 10000n) / cents(totals.net)) + "%";
});
const categoryNames: Record<string, string> = {
  DRUG: "药品（含饮片）",
  DEVICE: "医疗器械",
  FOOD: "食品",
  COSMETIC: "化妆品",
  DAILY: "日用品",
  OTHER: "其他商品",
  SERVICE: "服务",
};
const maximum = computed(() =>
  Math.max(
    1,
    ...(snapshot.value?.inventory?.groups.map((g) => Number(g.amount)) || []),
  ),
);
function openOrder(id: string) {
  detailOpen.value = false;
  void showRecord("sale", id, activeEntity.value);
}
</script>
<template>
  <div class="wb-heading" style="margin-bottom: 14px; flex-wrap: wrap">
    <p class="wb-subtitle">金额单位：元 · 人民币</p>
    <div class="wb-toolbar">
      <div class="wb-period" aria-label="经营统计周期">
        <button
          v-for="n in [7, 30]"
          :key="n"
          :aria-pressed="days === n"
          @click="days = n"
        >
          近{{ n }}日
        </button>
      </div>
      <button
        class="wb-button"
        :disabled="periodLoading || snapshotLoading"
        @click="
          loadPeriod();
          loadSnapshot();
        "
      >
        刷新经营数据
      </button>
    </div>
  </div>
  <WorkbenchStatus
    :loading="periodLoading"
    :error="periodError"
    :has-data="!!period"
    @retry="loadPeriod"
  />
  <p class="wb-note" style="margin-bottom: 10px">
    {{
      period
        ? `${period.start} — ${period.end} · 截至 ${localTime(period.as_of)}`
        : "按中国标准时间统计，包含尚未结束的今天。"
    }}
  </p>
  <section class="wb-kpis" aria-label="期间经营指标">
    <div class="wb-kpi">
      <label>销售额 <small>含税净销售</small></label
      ><strong>{{ money(period?.current.sales) }}</strong
      ><small v-if="period"
        >较前{{ days }}日
        {{ change(period.current.sales, period.previous.sales) }} ·
        本期今日未结束</small
      ><small v-else>等待真实统计</small>
    </div>
    <div class="wb-kpi">
      <label>毛利额 <small>不含税</small></label
      ><strong>{{ money(period?.current.profit) }}</strong
      ><small>毛利率 {{ margin }} · 按不含税净销售额计算</small>
    </div>
    <div class="wb-kpi">
      <label>回款 <small>净收款</small></label
      ><strong>{{ money(period?.current.cash) }}</strong
      ><small v-if="period"
        >较前{{ days }}日
        {{ change(period.current.cash, period.previous.cash) }} ·
        含往期应收</small
      ><small v-else>含已登记的零售收退款</small>
    </div>
  </section>
  <p
    v-if="
      period &&
      (period.current.sales === null || period.current.profit === null)
    "
    class="wb-note"
    role="status"
    style="margin: -6px 0 14px"
  >
    部分历史过账缺少金额或对应成本，受影响的期间指标显示“—”，需补齐来源信息后才能准确汇总。
  </p>
  <div class="wb-main-grid">
    <section class="wb-panel" aria-label="经营趋势">
      <div class="wb-panel-heading">
        <h3>经营趋势</h3>
        <div class="wb-period" aria-label="趋势指标">
          <button :aria-pressed="metric === 'sales'" @click="metric = 'sales'">
            销售额</button
          ><button :aria-pressed="metric === 'cash'" @click="metric = 'cash'">
            回款
          </button>
        </div>
      </div>
      <WorkbenchTrend v-if="period" :series="period.series" :metric="metric" />
      <div v-else class="wb-unavailable">
        <strong>{{
          periodLoading ? "正在汇总经营数据" : "经营趋势暂不可用"
        }}</strong>
        <p>读取成功后显示真实的逐日销售或回款，不以零值替代失败结果。</p>
      </div>
      <p class="wb-note">
        销售按过账业务日，扣除已过账退货；毛利扣除对应成本。回款按收款业务日期，含往期应收与零售，扣除有效退款、排除作废款项。
      </p>
    </section>
    <section class="wb-panel" aria-label="往来账款快照">
      <div class="wb-panel-heading">
        <h3>往来账款</h3>
        <span class="wb-note">当前余额快照</span>
      </div>
      <WorkbenchStatus
        :loading="snapshotLoading"
        :error="snapshotError"
        :has-data="!!snapshot"
        @retry="loadSnapshot"
      />
      <p class="wb-muted">应收余额</p>
      <div class="wb-large">{{ money(snapshot?.accounts.receivable) }}</div>
      <template v-if="snapshot"
        ><div
          class="wb-aging"
          :aria-label="`应收账龄：未到期${money(snapshot.accounts.current)}元，逾期30天内${money(snapshot.accounts.late)}元，逾期超过30天${money(snapshot.accounts.overdue)}元`"
          role="img"
        >
          <span
            v-for="key in ['current', 'late', 'overdue'] as const"
            :key="key"
            :style="{ flexGrow: Number(snapshot.accounts[key]) }"
          ></span>
        </div>
        <div class="wb-aging-labels">
          <div
            v-for="[key, title] in [
              ['current', '未到期'],
              ['late', '逾期≤30天'],
              ['overdue', '逾期>30天'],
            ]"
            :key="key"
          >
            <small>{{ title }}</small
            ><strong>{{
              money(snapshot.accounts[key as "current" | "late" | "overdue"])
            }}</strong>
          </div>
        </div>
        <div class="wb-payable">
          <span class="wb-muted">当前应付余额</span
          ><b>{{ money(snapshot.accounts.payable) }}</b>
        </div>
        <p class="wb-note">未来7日到期应付：未接入（应付账款尚无到期日）。</p>
        <button
          class="wb-link"
          style="margin-top: 12px"
          @click="openDetail('overdue')"
        >
          跟进 {{ snapshot.accounts.overdue_customers }} 家逾期客户 ↗
        </button></template
      >
    </section>
  </div>
  <div class="wb-bottom-grid">
    <section class="wb-panel" aria-label="库存结构">
      <div class="wb-panel-heading">
        <h3>库存结构</h3>
        <span class="wb-note">按成本金额</span>
      </div>
      <template v-if="snapshot?.inventory"
        ><div class="wb-large">
          {{ money(snapshot.inventory.amount) }}
          <small class="wb-note">元</small>
        </div>
        <div
          v-for="g in snapshot.inventory.groups"
          :key="g.category"
          class="wb-stock-row"
        >
          <span>{{ categoryNames[g.category] || g.category }}</span>
          <div class="wb-bar">
            <i :style="{ width: (Number(g.amount) / maximum) * 100 + '%' }"></i>
          </div>
          <strong>{{ money(g.amount) }}</strong>
        </div>
        <p v-if="!snapshot.inventory.groups.length" class="wb-empty">
          当前主体暂无有余额的实物库存。
        </p>
        <p class="wb-note" style="margin-top: 14px">
          主体批次成本池，含在途与隔离库存；不合计不同基本单位的数量。
        </p></template
      >
      <p v-else class="wb-empty">
        {{ snapshot ? "您没有库存读取权限。" : "等待读取库存快照。" }}
      </p>
    </section>
    <section class="wb-panel" aria-label="需要关注的经营事项">
      <div class="wb-panel-heading">
        <h3>需要关注</h3>
        <span class="wb-note">可核对明细</span>
      </div>
      <button
        class="wb-attention"
        :disabled="!snapshot"
        @click="openDetail('overdue')"
      >
        <span class="wb-icon danger">¥</span
        ><span
          ><b>逾期回款</b
          ><small
            >{{
              snapshot?.accounts.overdue_customers ?? "—"
            }}
            家客户待跟进</small
          ></span
        ><strong>{{ money(overdue) }}<small>元 ↗</small></strong></button
      ><button
        class="wb-attention"
        :disabled="!snapshot?.inventory"
        @click="openDetail('near')"
      >
        <span class="wb-icon warn">◷</span
        ><span
          ><b>近效期库存</b
          ><small
            >{{ snapshot?.inventory?.near_skus ?? "—" }} 个品规 ·
            未来90日内，不含已过期</small
          ></span
        ><strong
          >{{ money(snapshot?.inventory?.near_amount)
          }}<small>元 ↗</small></strong
        ></button
      ><button
        class="wb-attention"
        :disabled="!snapshot?.inventory"
        @click="openDetail('expired')"
      >
        <span class="wb-icon danger">!</span
        ><span
          ><b>已过期库存</b
          ><small
            >{{ snapshot?.inventory?.expired_skus ?? "—" }} 个品规 ·
            按实际失效时点</small
          ></span
        ><strong
          >{{ money(snapshot?.inventory?.expired_amount)
          }}<small>元 ↗</small></strong
        >
      </button>
      <div class="wb-attention">
        <span class="wb-icon">▤</span
        ><span
          ><b>低动销库存</b><small>未接入：尚未配置统一动销规则。</small></span
        ><strong>—</strong>
      </div>
    </section>
  </div>
  <div class="wb-foot">
    <span
      >账款与库存{{
        snapshot ? "截至 " + localTime(snapshot.as_of) : "尚未读取"
      }}；切换7／30日不改变此快照。</span
    ><button class="wb-link" @click="goPage('finance')">往来结算 ↗</button>
  </div>
  <el-drawer
    v-model="detailOpen"
    :title="names[detailKind]"
    class="wb-drawer"
    append-to-body
  >
    <div class="workbench">
      <p class="wb-note">
        按当前主体重新读取，数据可能随其他岗位办理而变化。{{
          detail ? "截至 " + localTime(detail.as_of) : ""
        }}
      </p>
      <WorkbenchStatus
        :loading="detailLoading"
        :error="detailError"
        :has-data="!!detail"
        @retry="loadDetail"
      /><template v-if="detail"
        ><p style="margin: 12px 0">
          共 {{ detail.total }}
          {{ detailKind === "overdue" ? "笔应收" : "条批次成本记录" }} · 合计
          {{ money(detail.amount) }} 元
        </p>
        <div class="wb-scroll">
          <table class="wb-table">
            <thead>
              <tr>
                <th>
                  {{ detailKind === "overdue" ? "客户 / 原单" : "商品 / 批次" }}
                </th>
                <th>
                  {{ detailKind === "overdue" ? "到期日期" : "有效期至" }}
                </th>
                <th>金额（元）</th>
                <th>查看</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in detail.items" :key="r.id">
                <td>
                  {{ r.name
                  }}<small
                    >{{ r.number
                    }}{{ r.lot_no ? " · 批号 " + r.lot_no : "" }}</small
                  ><small v-if="r.qty">{{ r.qty }} {{ r.base_unit }}</small>
                </td>
                <td>{{ r.day }}</td>
                <td>{{ money(r.amount) }}</td>
                <td>
                  <button
                    v-if="r.order_id && can('sales.read')"
                    class="wb-link"
                    @click="openOrder(r.order_id)"
                  >
                    销售原单 ↗</button
                  ><button
                    v-else-if="detailKind !== 'overdue'"
                    class="wb-link"
                    @click="
                      detailOpen = false;
                      goPage('inventory');
                    "
                  >
                    库存查询 ↗</button
                  ><span v-else class="wb-note">无原单读取权限</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!detail.items.length" class="wb-empty">当前范围暂无记录。</p>
        <div class="wb-pagination">
          <span>第 {{ offset / 20 + 1 }} 页 · 每页20条</span>
          <div>
            <button
              class="wb-button"
              :disabled="!offset || detailLoading"
              @click="offset -= 20"
            >
              上一页</button
            ><button
              class="wb-button"
              :disabled="!detail.has_more || detailLoading"
              @click="offset += 20"
            >
              下一页
            </button>
          </div>
        </div></template
      >
    </div>
  </el-drawer>
</template>
