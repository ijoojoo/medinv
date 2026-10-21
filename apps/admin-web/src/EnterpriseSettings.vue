<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { request } from "./api";
import { activeEntity } from "./entityScope";
import { goPage } from "./workspace";
import { can, useCommand } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import QualificationRules from "./QualificationRules.vue";
const emit = defineEmits<{ failure: [error: unknown] }>();
const tabs = [
  { id: "catalog", name: "基础资料" },
  { id: "purchase", name: "采购与销售" },
  { id: "warehouse", name: "仓储与质量" },
  { id: "qualification", name: "质量与准入" },
  { id: "finance", name: "财务结算" },
  { id: "members", name: "人员与权限" },
  { id: "reminders", name: "消息与提醒" },
  { id: "system", name: "系统级设置" },
];
const tab = ref("catalog"),
  loading = ref(false),
  loaded = ref(false),
  id = ref(crypto.randomUUID() as string),
  version = ref("0"),
  baseline = ref(""),
  reason = ref("");
const values = ref({
  reminders_enabled: true,
  work_queue_enabled: true,
  certificate_reminders_enabled: true,
  scope_reminders_enabled: true,
  catalog_review_reminders_enabled: true,
  maintenance_reminders_enabled: true,
  certificate_notice_days: 30,
  maintenance_notice_days: 7,
});
const dirty = computed(
  () =>
    loaded.value &&
    (JSON.stringify(values.value) !== baseline.value || !!reason.value),
);
const { busy, execute } = useCommand((e) => emit("failure", e));
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    "enterprise-settings",
  ),
);
const links = [
  {
    tab: "finance",
    title: "结算方式字典",
    description: "维护企业结算方式选项；已有单据按原结算记录保留。",
    page: "dictionaries",
    permission: "catalog.read",
  },
  {
    tab: "finance",
    title: "往来结算",
    description: "查看应收应付、收付款及核销记录。",
    page: "finance",
    permission: "finance.read",
  },
  {
    tab: "catalog",
    title: "数据字典",
    description: "基础选项、企业扩展、分级部门、启停与排序。",
    page: "dictionaries",
    permission: "catalog.read",
  },
  {
    tab: "catalog",
    title: "商品字段与分类规则",
    description: "商品品类、必填字段、资料分组、编码与校验规则。",
    page: "productFields",
    permission: "catalog.configure",
  },
  {
    tab: "purchase",
    title: "经营范围与交易准入",
    description:
      "本企业范围配置与交易试算；往来单位范围在单位资料中随主体审核。",
    page: "admission",
    permission: "catalog.read",
  },
  {
    tab: "purchase",
    title: "审批流设计",
    description: "按业务设计审核节点、角色、会签或或签；保存设计稿。",
    page: "approval-design",
    permission: "catalog.configure",
  },
  {
    tab: "purchase",
    title: "采购结论用语",
    description: "采购审核、合格与拒收结论在数据字典按组维护。",
    page: "dictionaries",
    permission: "catalog.read",
  },
  {
    tab: "warehouse",
    title: "仓库与库位",
    description: "管理经营主体下的仓库、库位及其启停。",
    page: "warehouses",
    permission: "inventory.read",
  },
  {
    tab: "warehouse",
    title: "质量处置",
    description: "商品停售、解除停售及库存质量状态处置。",
    page: "quality",
    permission: "quality.read",
  },
  {
    tab: "members",
    title: "职员资料",
    description: "职员工号、部门、岗位、联系方式与任职状态。",
    page: "staff",
    permission: "members.read",
  },
  {
    tab: "members",
    title: "成员与权限",
    description: "账号授权、业务角色与经营主体访问范围。",
    page: "members",
    permission: "members.read",
  },
  {
    tab: "members",
    title: "个人界面与操作",
    description: "导航布局、密度、主题等个人使用偏好。",
    page: "appearance",
    permission: "",
  },
];
async function load() {
  loading.value = true;
  try {
    const data = await request<{
      items: { id: string; version: string; payload: typeof values.value }[];
    }>(`/api/v1/management/settings?entity_id=${activeEntity.value}`);
    if (data.items[0]) {
      id.value = data.items[0].id;
      version.value = data.items[0].version;
      values.value = { ...values.value, ...data.items[0].payload };
    }
    baseline.value = JSON.stringify(values.value);
    loaded.value = true;
    reason.value = "";
  } catch (e) {
    emit("failure", e);
  } finally {
    loading.value = false;
  }
}
async function save() {
  await execute(
    `/api/v1/management/settings/${id.value}`,
    {
      entity_id: activeEntity.value,
      expected_version: version.value,
      payload: values.value,
      reason: reason.value,
    },
    load,
  );
}
onMounted(load);
</script>
<template>
  <section
    class="panel enterprise-settings"
    aria-label="企业设置"
    v-loading="loading"
  >
    <header class="master-toolbar">
      <div>
        <h2>企业设置</h2>
        <small
          >配置按当前经营主体生效；共享资料配置按其原有企业范围管理。</small
        >
      </div>
    </header>
    <nav class="master-tabs" aria-label="设置功能分组">
      <button
        v-for="t in tabs"
        :key="t.id"
        :class="{ active: tab === t.id }"
        @click="tab = t.id"
      >
        {{ t.name }}
      </button>
    </nav>
    <QualificationRules
      v-show="tab === 'qualification'"
      @failure="emit('failure', $event)"
    />
    <div
      v-if="!['qualification', 'reminders', 'system'].includes(tab)"
      class="settings-options"
    >
      <button
        v-for="link in links.filter(
          (l) => l.tab === tab && (!l.permission || can(l.permission)),
        )"
        :key="link.title"
        @click="goPage(link.page)"
      >
        <strong>{{ link.title }} <span>↗</span></strong>
        <p>{{ link.description }}</p>
      </button>
    </div>
    <form
      v-else-if="tab === 'reminders'"
      class="business-form reminder-settings"
      @submit.prevent="save"
    >
      <h3>站内提醒规则</h3>
      <fieldset :disabled="busy || !loaded || !can('catalog.configure')">
        <label
          ><input v-model="values.reminders_enabled" type="checkbox" />
          启用站内消息与提醒</label
        ><label
          ><input v-model="values.work_queue_enabled" type="checkbox" />
          汇总业务待办提醒</label
        ><label
          ><input
            v-model="values.certificate_reminders_enabled"
            type="checkbox"
          />证件到期提醒（商品、往来单位）</label
        ><label
          ><input
            v-model="values.scope_reminders_enabled"
            type="checkbox"
          />经营范围异常提醒</label
        ><label
          ><input
            v-model="values.catalog_review_reminders_enabled"
            type="checkbox"
          />主体资料待审提醒</label
        ><label
          ><input
            v-model="values.maintenance_reminders_enabled"
            type="checkbox"
          />养护到期提醒</label
        ><label
          >资质到期提前提醒天数<input
            v-model.number="values.certificate_notice_days"
            type="number"
            min="0"
            max="365"
            required /></label
        ><label
          >养护到期提前提醒天数<input
            v-model.number="values.maintenance_notice_days"
            type="number"
            min="0"
            max="365"
            required
        /></label>
        <p class="form-note">
          过期事项仍会显示。关闭提醒开关不会关闭已有处理事项，也不改变业务准入规则。消息中心打开或刷新时重新核查；处理记录中的“确认关闭”仍校验原资料或原单。
        </p>
        <label
          >变更原因<input v-model="reason" maxlength="200" required /></label
        ><el-button
          native-type="submit"
          type="primary"
          :disabled="!dirty"
          :loading="busy"
          >保存提醒设置</el-button
        >
      </fieldset>
    </form>
    <div v-else-if="tab === 'system'" class="settings-boundary">
      <h3>系统级设置</h3>
      <p>
        租户管理、平台标准字典、系统开关、第三方接入凭据、升级与运维配置属于系统管理范围。
      </p>
      <p>
        独立系统管理页面按后续阶段建设；企业管理员在此查看范围说明，不能修改平台配置。
      </p>
    </div>
  </section>
</template>
<style scoped>
.enterprise-settings {
  max-width: 1380px;
  margin: auto;
}
.master-toolbar small {
  display: block;
  margin-top: 8px;
  color: var(--ui-muted);
}
.master-tabs {
  overflow-x: auto;
  white-space: nowrap;
}
.settings-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 24px 0;
}
.settings-options button {
  border: 1px solid var(--ui-border);
  border-radius: 9px;
  background: var(--ui-surface, #fff);
  padding: 20px;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: var(--ui-text);
}
.settings-options button:hover {
  border-color: var(--ui-accent);
  background: var(--ui-tint);
}
.settings-options strong {
  display: flex;
  justify-content: space-between;
}
.settings-options p,
.settings-boundary {
  color: var(--ui-muted);
  line-height: 1.8;
  font-size: 13px;
}
.reminder-settings {
  max-width: 700px;
  margin: 24px 0;
}
.reminder-settings fieldset {
  border: 0;
  display: grid;
  gap: 16px;
}
.reminder-settings label {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}
.settings-boundary {
  padding: 20px 0;
}
@media (max-width: 700px) {
  .settings-options {
    grid-template-columns: 1fr;
  }
}
</style>
