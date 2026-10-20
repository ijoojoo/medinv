<script setup lang="ts">
import { organizationTypes, type PartyProfile } from "./partyProfile";

defineProps<{
  profile: PartyProfile;
  editable: boolean;
  disabled: boolean;
  pending: boolean;
}>();
const emit = defineEmits<{
  maintain: [field: string];
  open: [tab: string];
}>();
const sections = [
  {
    title: "日常联系",
    action: "维护联系信息",
    focus: "contact_name",
    fields: [
      ["contact_name", "联系人"],
      ["contact_phone", "联系电话"],
      ["delivery_address", "默认收发货地址"],
    ],
  },
  {
    title: "单位识别",
    action: "维护简称与检索",
    focus: "short_name",
    fields: [
      ["short_name", "单位简称"],
      ["search_alias", "检索别名 / 助记码"],
      ["organization_type", "机构性质"],
      ["legal_representative", "法定代表人 / 负责人"],
      ["registered_address", "注册地址"],
    ],
  },
] as const;
const extensions = [
  {
    tab: "quality",
    title: "质量管控",
    description: "经营资质、范围与核验档案",
  },
  { tab: "insurance", title: "医保信息", description: "医保机构编码" },
  { tab: "trace", title: "追溯信息", description: "码上放心单位编码" },
  {
    tab: "platforms",
    title: "第三方对接",
    description: "美团、饿了么、淘宝、旺店通及其他系统编码",
  },
];
</script>
<template>
  <div class="party-overview">
    <p v-if="pending" class="form-note saved-notice" role="status">
      经营信息有未保存的修改，以下显示已保存内容。进入维护可继续编辑。
    </p>
    <div class="overview-grid">
      <section
        v-for="section in sections"
        :key="section.title"
        class="overview-section"
        :aria-label="section.title"
      >
        <div class="overview-heading">
          <h3>{{ section.title }}</h3>
          <el-button
            v-if="editable"
            link
            type="primary"
            :disabled="disabled"
            @click="emit('maintain', section.focus)"
            >{{ section.action }}</el-button
          >
        </div>
        <dl>
          <template v-for="[key, label] in section.fields" :key="key">
            <dt>{{ label }}</dt>
            <dd :class="{ empty: !profile[key] }">
              {{
                key === "organization_type"
                  ? organizationTypes[profile[key]] || profile[key]
                  : profile[key] || "未填写"
              }}
            </dd>
          </template>
        </dl>
      </section>
    </div>
    <p class="form-note overview-note">
      联系信息、简称与助记码可直接维护；名称、机构性质、注册地址及资质通过“修订资料”调整。
    </p>
    <section class="extension-section" aria-label="扩展资料入口">
      <h3>扩展资料</h3>
      <div class="extension-links">
        <button
          v-for="item in extensions"
          :key="item.tab"
          type="button"
          :disabled="disabled"
          @click="emit('open', item.tab)"
        >
          <span class="extension-title"
            >{{ item.title }} <span aria-hidden="true">↗</span></span
          >
          <span class="extension-description">{{ item.description }}</span>
        </button>
      </div>
    </section>
  </div>
</template>
<style scoped>
.overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.overview-section {
  min-width: 0;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  overflow: hidden;
}
.overview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px 14px;
  background: var(--ui-tint);
}
h3 {
  font-size: 15px;
  margin: 0;
}
dl {
  display: grid;
  grid-template-columns: minmax(100px, 0.35fr) minmax(0, 1fr);
  gap: 10px 16px;
  margin: 14px;
  font-size: 14px;
  line-height: 1.6;
}
dt,
.empty {
  color: var(--ui-muted);
}
dd {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.overview-note {
  margin: 10px 0 18px;
}
.saved-notice {
  color: var(--ui-accent);
}
.extension-links {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 12px 0 18px;
}
.extension-links button {
  min-width: 0;
  padding: 12px;
  text-align: left;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: var(--surface, #fff);
  font: inherit;
  cursor: pointer;
}
.extension-links button:hover:not(:disabled) {
  border-color: var(--ui-accent);
  background: var(--ui-tint);
}
.extension-links button:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}
.extension-links button:disabled {
  opacity: 0.55;
  cursor: default;
}
.extension-title {
  display: flex;
  justify-content: space-between;
  color: var(--ui-accent);
  font-weight: 600;
}
.extension-description {
  display: block;
  color: var(--ui-muted);
  font-size: 13px;
  line-height: 1.6;
  margin-top: 6px;
  overflow-wrap: anywhere;
}
@media (max-width: 800px) {
  .overview-grid {
    grid-template-columns: 1fr;
  }
  .extension-links {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 450px) {
  .extension-links {
    grid-template-columns: 1fr;
  }
}
</style>
