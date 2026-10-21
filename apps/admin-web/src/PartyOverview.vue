<script setup lang="ts">
import { organizationTypes, type PartyProfile } from "./partyProfile";

defineProps<{
  profile: PartyProfile;
  pending: boolean;
}>();
const sections = [
  {
    title: "日常联系",
    fields: [
      ["contact_name", "联系人"],
      ["contact_phone", "联系电话"],
      ["delivery_address", "默认收发货地址"],
    ],
  },
  {
    title: "单位识别",
    fields: [
      ["short_name", "单位简称"],
      ["search_alias", "检索别名 / 助记码"],
      ["organization_type", "机构性质"],
      ["legal_representative", "法定代表人 / 负责人"],
      ["registered_address", "注册地址"],
    ],
  },
] as const;
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
        </div>
        <dl>
          <div v-for="[key, label] in section.fields" :key="key">
            <dt>{{ label }}</dt>
            <dd :class="{ empty: !profile[key] }">
              {{
                key === "organization_type"
                  ? organizationTypes[profile[key]] || profile[key]
                  : profile[key] || "未填写"
              }}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  </div>
</template>
<style scoped>
.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
}
.overview-section {
  min-width: 0;
  border-top: 1px solid var(--ui-border);
  border-radius: 0;
  overflow: hidden;
}
.overview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  padding: 18px 0 6px;
  background: transparent;
}
h3 {
  font-size: 15px;
  margin: 0;
}
dl {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 16px;
  margin: 14px 0 24px;
  font-size: 14px;
  line-height: 1.6;
}
dt {
  margin-bottom: 8px;
  font-size: 12px;
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
@media (max-width: 800px) {
  dl {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .overview-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 450px) {
  dl {
    grid-template-columns: 1fr;
  }
}
</style>
