<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import type { CatalogDictionary } from "./catalogDictionaries";
import DateInput from "./DateInput.vue";
import { localTime } from "./sales";
type Kind = "staff" | "maintenance" | "approval_design";
import { clearReference, type ReferenceTarget } from "./workspace";
const props = defineProps<{ kind: Kind; initialTarget?: ReferenceTarget }>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const titles = {
  staff: "职员资料",
  maintenance: "质量台账",
  approval_design: "审批流设计",
};
const pages = {
  staff: "staff",
  maintenance: "quality-ledgers",
  approval_design: "approval-design",
};
const permission = {
  staff: "members.manage",
  maintenance: "quality.write",
  approval_design: "catalog.configure",
};
type Row = {
  id: string;
  version: string;
  payload: Record<string, unknown>;
  updated_at: string;
  history?: {
    created_at: string;
    created_by: string;
    detail: { reason: string; version: string };
  }[];
};
type Step = {
  id: string;
  name: string;
  role: "QUALITY" | "ADMIN" | "OWNER";
  mode: "ANY" | "ALL";
};
const rows = ref<Row[]>([]),
  selected = ref<Row | null>(null),
  editing = ref(false),
  search = ref(""),
  offset = ref(0),
  more = ref(false),
  loading = ref(false),
  failed = ref(false);
const reason = ref(""),
  baseline = ref(""),
  recordId = ref("");
const staff = ref({
  code: "",
  name: "",
  department_id: null as string | null,
  job: "",
  phone: "",
  email: "",
  joined_on: null as string | null,
  left_on: null as string | null,
  status: "ACTIVE",
  note: "",
});
const maintenance = ref({
  product_id: "",
  lot_no: "",
  checked_on: "",
  next_due: null as string | null,
  type_id: null as string | null,
  operator_name: "",
  checks: "",
  result: "NORMAL",
  actions: "",
  state: "DRAFT",
});
const approval = ref({
  name: "",
  business: "PURCHASE",
  steps: [] as Step[],
  note: "",
});
const dictionary = ref<CatalogDictionary | null>(null),
  products = ref<{ id: string; values: { name: string; code: string } }[]>([]),
  productSearch = ref("");
const payload = computed(() =>
  props.kind === "staff"
    ? staff.value
    : props.kind === "maintenance"
      ? maintenance.value
      : approval.value,
);
const dirty = computed(
  () =>
    editing.value &&
    (JSON.stringify(payload.value) !== baseline.value || !!reason.value),
);
const writable = computed(() => can(permission[props.kind]));
const locked = computed(
  () =>
    !writable.value ||
    (props.kind === "maintenance" &&
      selected.value?.payload.state !== undefined &&
      selected.value.payload.state !== "DRAFT"),
);
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    pages[props.kind],
  ),
);
let epoch = 0;
onUnmounted(() => epoch++);
async function load(reset = false) {
  if (reset) offset.value = 0;
  const token = ++epoch;
  loading.value = true;
  failed.value = false;
  try {
    const data = await request<{ items: Row[]; has_more: boolean }>(
      `/api/v1/management/${props.kind}?entity_id=${activeEntity.value}&search=${encodeURIComponent(search.value)}&offset=${offset.value}`,
    );
    if (token === epoch) {
      rows.value = data.items;
      more.value = data.has_more;
    }
  } catch (e) {
    if (token === epoch) {
      failed.value = true;
      emit("failure", e);
    }
  } finally {
    if (token === epoch) loading.value = false;
  }
}
async function loadDictionary() {
  if (props.kind === "approval_design") return;
  try {
    dictionary.value = await request(
      `/api/v1/catalog/dictionaries/${props.kind === "staff" ? "department" : "maintenance_type"}`,
    );
  } catch (e) {
    emit("failure", e);
  }
}
async function findProducts() {
  try {
    const data = await request<{ items: typeof products.value }>(
      `/api/v1/catalog/master/products?search=${encodeURIComponent(productSearch.value)}`,
    );
    products.value = data.items;
  } catch (e) {
    emit("failure", e);
  }
}
function newStep() {
  approval.value.steps.push({
    id: crypto.randomUUID(),
    name: `第 ${approval.value.steps.length + 1} 级审核`,
    role: "QUALITY",
    mode: "ANY",
  });
}
function moveStep(i: number, d: number) {
  const n = i + d;
  if (n < 0 || n >= approval.value.steps.length) return;
  [approval.value.steps[i], approval.value.steps[n]] = [
    approval.value.steps[n],
    approval.value.steps[i],
  ];
}
async function open(row?: Row) {
  loading.value = true;
  try {
    const current = row
      ? await request<Row>(
          `/api/v1/management/${props.kind}/${row.id}?entity_id=${activeEntity.value}`,
        )
      : null;
    selected.value = current;
    recordId.value = current?.id || crypto.randomUUID();
    reason.value = "";
    if (props.kind === "staff")
      staff.value = current
        ? (current.payload as typeof staff.value)
        : {
            code: "",
            name: "",
            department_id: null,
            job: "",
            phone: "",
            email: "",
            joined_on: null,
            left_on: null,
            status: "ACTIVE",
            note: "",
          };
    if (props.kind === "maintenance")
      maintenance.value = current
        ? (current.payload as typeof maintenance.value)
        : {
            product_id: "",
            lot_no: "",
            checked_on: new Intl.DateTimeFormat("en-CA", {
              timeZone: "Asia/Shanghai",
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
            }).format(new Date()),
            next_due: null,
            type_id: null,
            operator_name: "",
            checks: "",
            result: "NORMAL",
            actions: "",
            state: "DRAFT",
          };
    if (props.kind === "approval_design") {
      approval.value = current
        ? (current.payload as typeof approval.value)
        : { name: "", business: "PURCHASE", steps: [], note: "" };
      if (!current) newStep();
    }
    // Detach editable values from the saved detail and audit snapshot.
    staff.value = JSON.parse(JSON.stringify(staff.value));
    maintenance.value = JSON.parse(JSON.stringify(maintenance.value));
    approval.value = JSON.parse(JSON.stringify(approval.value));
    baseline.value = JSON.stringify(payload.value);
    editing.value = true;
    await loadDictionary();
    if (props.kind === "maintenance") await findProducts();
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
}
async function close() {
  if (busy.value) return;
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        "当前资料有未保存修改，是否放弃？",
        "返回查询",
        { confirmButtonText: "放弃修改", cancelButtonText: "继续维护" },
      );
    } catch {
      return;
    }
  }
  editing.value = false;
  clearReference(pages[props.kind]);
  reason.value = "";
}
async function save() {
  if (props.kind === "staff") {
    staff.value.joined_on ||= null;
    staff.value.left_on ||= null;
  }
  if (props.kind === "maintenance") maintenance.value.next_due ||= null;
  await execute<{ id: string }>(
    `/api/v1/management/${props.kind}/${recordId.value}`,
    {
      entity_id: activeEntity.value,
      expected_version: selected.value?.version || "0",
      payload: payload.value,
      reason: reason.value,
    },
    async (r) => {
      baseline.value = JSON.stringify(payload.value);
      reason.value = "";
      await open({ id: r.id } as Row);
      await load();
    },
  );
}
async function voidRecord() {
  if (!reason.value.trim()) return;
  maintenance.value.state = "VOID";
  await save();
}
function cell(row: Row, key: string) {
  const value = row.payload[key];
  if (value == null || value === "") return "未填写";
  if (key === "department_id")
    return (
      dictionary.value?.items.find((i) => i.id === value)?.value ||
      String(value)
    );
  if (key === "status") return value === "ACTIVE" ? "在职" : "离职";
  if (key === "state")
    return (
      { DRAFT: "草稿", COMPLETED: "已完成", VOID: "已作废" } as Record<
        string,
        string
      >
    )[String(value)];
  if (key === "result") return value === "NORMAL" ? "正常" : "异常";
  if (key === "business")
    return (
      {
        PURCHASE: "采购入库",
        SALES: "销售出库",
        PRODUCT: "商品审核",
        PARTY: "往来单位审核",
        QUALITY: "质量处置",
      } as Record<string, string>
    )[String(value)];
  return String(value);
}
const columns = computed(() =>
  props.kind === "staff"
    ? [
        ["code", "职员编码"],
        ["name", "姓名"],
        ["department_id", "部门"],
        ["job", "岗位"],
        ["phone", "联系电话"],
        ["status", "状态"],
      ]
    : props.kind === "maintenance"
      ? [
          ["operator_name", "养护人员"],
          ["lot_no", "批号"],
          ["checked_on", "养护日期"],
          ["next_due", "下次日期"],
          ["result", "检查结果"],
          ["state", "记录状态"],
        ]
      : [
          ["name", "设计名称"],
          ["business", "适用业务"],
          ["note", "设计说明"],
        ],
);
watch(
  () => props.initialTarget?.token,
  () => {
    if (props.initialTarget) void open({ id: props.initialTarget.id } as Row);
  },
);
onMounted(() => {
  if (props.initialTarget) void open({ id: props.initialTarget.id } as Row);
  void load();
  void loadDictionary();
});
</script>
<template>
  <section
    class="panel management-page"
    :aria-label="titles[kind]"
    v-loading="loading"
  >
    <header class="master-toolbar">
      <div>
        <h2>{{ titles[kind] }}</h2>
        <small>{{
          kind === "staff"
            ? "职员档案与登录账号分开维护；离职不自动撤销账号权限。"
            : kind === "maintenance"
              ? "养护检查与处理留痕；记录本身不改变库存或购销状态。"
              : "设计稿保存、节点排序与流程预览。设计稿尚未接管业务审批。"
        }}</small>
      </div>
      <el-button v-if="editing" :disabled="busy" @click="close"
        >返回查询</el-button
      ><el-button v-else-if="writable" type="primary" @click="open()">{{
        kind === "staff"
          ? "新增职员"
          : kind === "maintenance"
            ? "新增养护记录"
            : "新建审批设计"
      }}</el-button>
    </header>
    <p v-if="failed" role="alert">读取失败，请重试查询。</p>
    <template v-if="!editing"
      ><form class="business-toolbar" @submit.prevent="load(true)">
        <label
          >查询<input
            v-model="search"
            maxlength="100"
            placeholder="编码、名称或记录内容" /></label
        ><el-button native-type="submit">查询</el-button
        ><el-button
          @click="
            search = '';
            load(true);
          "
          >重置</el-button
        >
      </form>
      <div class="master-table-scroll">
        <table class="master-list-table">
          <thead>
            <tr>
              <th v-for="c in columns" :key="c[0]">{{ c[1] }}</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td v-for="c in columns" :key="c[0]">{{ cell(row, c[0]) }}</td>
              <td>{{ localTime(row.updated_at) }}</td>
              <td>
                <button class="master-text-button" @click="open(row)">
                  {{ writable ? "维护" : "查看" }}
                </button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td :colspan="columns.length + 2" class="master-empty-tab">
                暂无记录
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="master-pagination">
        <el-button
          :disabled="!offset || loading"
          @click="
            offset -= 50;
            load();
          "
          >上一页</el-button
        ><span>{{ Math.floor(offset / 50) + 1 }} 页</span
        ><el-button
          :disabled="!more || loading"
          @click="
            offset += 50;
            load();
          "
          >下一页</el-button
        >
      </div></template
    >
    <form v-else class="business-form management-editor" @submit.prevent="save">
      <div class="master-tabs">
        <span class="active">{{
          kind === "staff"
            ? "基本信息"
            : kind === "maintenance"
              ? "养护记录"
              : "流程设计"
        }}</span
        ><span
          >{{ selected ? "v" + selected.version : "新建"
          }}{{ dirty ? " · 未保存" : "" }}</span
        >
      </div>
      <fieldset :disabled="busy || locked">
        <div v-if="kind === 'staff'" class="form-grid">
          <label
            >职员编码<input
              v-model="staff.code"
              required
              maxlength="60"
              :disabled="!!selected"
              pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" /></label
          ><label
            >姓名<input v-model="staff.name" required maxlength="200" /></label
          ><label
            >部门<select v-model="staff.department_id">
              <option :value="null">未指定</option>
              <option
                v-for="i in dictionary?.items.filter(
                  (i) => i.active || i.id === staff.department_id,
                )"
                :key="i.id"
                :value="i.id"
              >
                {{ i.value }}{{ i.active ? "" : "（已停用）" }}
              </option>
            </select></label
          ><label>岗位<input v-model="staff.job" maxlength="100" /></label
          ><label>联系电话<input v-model="staff.phone" maxlength="100" /></label
          ><label
            >电子邮箱<input
              v-model="staff.email"
              type="email"
              maxlength="100" /></label
          ><label>入职日期<DateInput v-model="staff.joined_on" /></label
          ><label>离职日期<DateInput v-model="staff.left_on" /></label
          ><label
            >任职状态<select v-model="staff.status">
              <option value="ACTIVE">在职</option>
              <option value="LEFT">离职</option>
            </select></label
          ><label class="full-width"
            >备注<textarea v-model="staff.note" maxlength="500"></textarea>
          </label>
        </div>
        <div v-else-if="kind === 'maintenance'" class="form-grid">
          <div class="full-width business-toolbar">
            <label
              >查找养护商品<input
                v-model="productSearch"
                maxlength="100" /></label
            ><el-button @click="findProducts">查找商品</el-button>
          </div>
          <label class="full-width"
            >养护商品<select v-model="maintenance.product_id" required>
              <option value="">请选择商品</option>
              <option
                v-if="
                  maintenance.product_id &&
                  !products.some((p) => p.id === maintenance.product_id)
                "
                :value="maintenance.product_id"
              >
                已关联商品 {{ maintenance.product_id }}
              </option>
              <option v-for="p in products" :key="p.id" :value="p.id">
                {{ p.values.name }} · {{ p.values.code }}
              </option>
            </select></label
          ><label
            >批号<input v-model="maintenance.lot_no" maxlength="100" /></label
          ><label
            >养护人员<input
              v-model="maintenance.operator_name"
              required
              maxlength="200" /></label
          ><label
            >养护日期<DateInput
              v-model="maintenance.checked_on"
              required /></label
          ><label
            >下次养护日期<DateInput v-model="maintenance.next_due" /></label
          ><label
            >养护类型<select v-model="maintenance.type_id">
              <option :value="null">未指定</option>
              <option
                v-for="i in dictionary?.items.filter(
                  (i) => i.active || i.id === maintenance.type_id,
                )"
                :key="i.id"
                :value="i.id"
              >
                {{ i.value }}
              </option>
            </select></label
          ><label
            >检查结果<select v-model="maintenance.result">
              <option value="NORMAL">正常</option>
              <option value="ABNORMAL">异常</option>
            </select></label
          ><label class="full-width"
            >检查项目与情况<textarea
              v-model="maintenance.checks"
              required
              maxlength="200"
            ></textarea></label
          ><label class="full-width"
            >处理说明<textarea
              v-model="maintenance.actions"
              :required="maintenance.result === 'ABNORMAL'"
              maxlength="500"
            ></textarea></label
          ><label
            >记录状态<select v-model="maintenance.state">
              <option value="DRAFT">草稿</option>
              <option value="COMPLETED">完成并留档</option>
            </select></label
          >
        </div>
        <div v-else>
          <div class="form-grid">
            <label
              >设计名称<input
                v-model="approval.name"
                required
                maxlength="200" /></label
            ><label
              >适用业务<select v-model="approval.business">
                <option value="PURCHASE">采购入库</option>
                <option value="SALES">销售出库</option>
                <option value="PRODUCT">商品审核</option>
                <option value="PARTY">往来单位审核</option>
                <option value="QUALITY">质量处置</option>
              </select></label
            >
          </div>
          <div class="approval-flow">
            <div class="approval-terminal">提交申请</div>
            <template v-for="(step, i) in approval.steps" :key="step.id"
              ><span class="approval-arrow">↓</span>
              <section class="approval-node">
                <div class="approval-node-heading">
                  <strong>审批节点 {{ i + 1 }}</strong
                  ><el-button :disabled="i === 0" @click="moveStep(i, -1)"
                    >上移</el-button
                  ><el-button
                    :disabled="i === approval.steps.length - 1"
                    @click="moveStep(i, 1)"
                    >下移</el-button
                  ><el-button
                    :disabled="approval.steps.length === 1"
                    @click="approval.steps.splice(i, 1)"
                    >移除</el-button
                  >
                </div>
                <div class="form-grid">
                  <label
                    >节点名称<input
                      v-model="step.name"
                      required
                      maxlength="200" /></label
                  ><label
                    >审批角色<select v-model="step.role">
                      <option value="QUALITY">质量审核员</option>
                      <option value="ADMIN">企业管理员</option>
                      <option value="OWNER">企业所有者</option>
                    </select></label
                  ><label
                    >审批方式<select v-model="step.mode">
                      <option value="ANY">或签（一人通过）</option>
                      <option value="ALL">会签（全部通过）</option>
                    </select></label
                  >
                </div>
                <small
                  >申请人与资料修订人不得审核自己的申请。驳回后返回申请人重新提交。</small
                >
              </section></template
            ><span class="approval-arrow">↓</span>
            <div class="approval-terminal">审批完成 · 交回原业务办理</div>
          </div>
          <el-button :disabled="approval.steps.length >= 10" @click="newStep"
            >＋ 添加审批节点</el-button
          ><label
            >设计说明<textarea
              v-model="approval.note"
              maxlength="500"
            ></textarea>
          </label>
          <p class="form-note">
            当前可保存设计、查看节点和审计记录。运行时人员分配、条件分支、催办、流程版本绑定及业务切换将在审批引擎接入时启用。
          </p>
        </div>
      </fieldset>
      <div v-if="writable" class="form-actions">
        <label
          >变更原因<input
            v-model="reason"
            required
            maxlength="200"
            :disabled="busy" /></label
        ><el-button
          v-if="!locked"
          type="primary"
          native-type="submit"
          :loading="busy"
          >{{
            kind === "approval_design" ? "保存设计稿" : "保存资料"
          }}</el-button
        ><el-button
          v-else-if="
            kind === 'maintenance' && selected?.payload.state === 'COMPLETED'
          "
          type="danger"
          :disabled="!reason.trim() || busy"
          @click="voidRecord"
          >作废记录</el-button
        >
      </div>
      <details v-if="selected?.history?.length">
        <summary>变更记录</summary>
        <p v-for="h in selected.history" :key="h.created_at">
          {{ localTime(h.created_at) }} · {{ h.detail.reason }} ·
          {{ h.created_by }}
        </p>
      </details>
    </form>
  </section>
</template>
<style scoped>
.management-page {
  max-width: 1380px;
  margin: 0 auto;
}
.master-toolbar small {
  display: block;
  margin-top: 8px;
  line-height: 1.7;
  color: var(--ui-muted);
}
.management-editor {
  max-width: 1100px;
  margin: auto;
}
.management-editor .form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.full-width {
  grid-column: 1/-1;
}
.management-editor fieldset {
  border: 0;
  padding: 14px 0;
}
.management-editor .master-tabs {
  display: flex;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid var(--ui-border);
}
.approval-flow {
  max-width: 780px;
  margin: 22px auto;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.approval-node {
  width: 100%;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  padding: 16px;
  background: var(--ui-tint);
  box-sizing: border-box;
}
.approval-node-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.approval-node-heading strong {
  margin-right: auto;
}
.approval-node small {
  color: var(--ui-muted);
}
.approval-terminal {
  border-radius: 20px;
  padding: 9px 24px;
  background: var(--ui-soft);
  color: var(--ui-accent);
}
.approval-arrow {
  padding: 8px;
  color: var(--ui-muted);
}
@media (max-width: 800px) {
  .management-editor .form-grid {
    grid-template-columns: 1fr;
  }
  .approval-node-heading {
    flex-wrap: wrap;
  }
}
</style>
