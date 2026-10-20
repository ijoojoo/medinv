<script setup lang="ts">
import {
  partyFields,
  partyFieldTab,
  organizationTypes,
  type PartyProfile,
} from "./partyProfile";
const props = defineProps<{
  readonly?: boolean;
  qualificationOnly?: boolean;
  section?: "basic" | "operations" | "quality";
}>();
const businessKeys = [
  "search_alias",
  "short_name",
  "contact_name",
  "contact_phone",
  "delivery_address",
];
const fieldsInGroup = (group: string) =>
  partyFields.filter(
    (f) =>
      f.group === group &&
      (!props.section || partyFieldTab(f.key) === props.section) &&
      (!props.qualificationOnly || !businessKeys.includes(f.key)),
  );
const model = defineModel<PartyProfile>({ required: true });
const groups = [...new Set(partyFields.map((f) => f.group))];
</script>
<template>
  <section
    v-for="group in groups.filter((g) => fieldsInGroup(g).length)"
    :key="group"
    class="party-profile-group full-width"
  >
    <h3>{{ group }}</h3>
    <p v-if="group === '核验档案'" class="form-note">
      填写已核验的纸质或电子档案编号，方便追查原件。编号不替代原件、有效期核验和独立审核。
    </p>
    <dl v-if="readonly" class="facts">
      <template v-for="field in fieldsInGroup(group)" :key="field.key">
        <dt>{{ field.label }}</dt>
        <dd>
          {{
            field.key === "organization_type"
              ? organizationTypes[model[field.key]]
              : model[field.key] || "未填写"
          }}
        </dd>
      </template>
    </dl>
    <div v-else class="form-grid">
      <label
        v-for="field in fieldsInGroup(group)"
        :key="field.key"
        :class="{ 'full-width': field.max > 200 }"
      >
        {{ field.label }}
        <select
          v-if="field.key === 'organization_type'"
          v-model="model[field.key]"
        >
          <option
            v-for="(name, key) in organizationTypes"
            :key="key"
            :value="key"
          >
            {{ name }}
          </option>
        </select>
        <textarea
          v-else-if="field.max === 1000"
          v-model="model[field.key]"
          :maxlength="field.max"
          rows="2"
        />
        <input
          v-else
          v-model="model[field.key]"
          :maxlength="field.max"
          :type="field.key === 'contact_phone' ? 'tel' : 'text'"
          :pattern="
            field.key === 'contact_phone'
              ? '[0-9+\\(\\)（） .转分机\\-]*'
              : undefined
          "
        />
      </label>
    </div>
  </section>
</template>
<style scoped>
.party-profile-group {
  border-top: 1px solid var(--line);
  padding: 12px 0;
  min-width: 0;
}
h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
dd {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
</style>
