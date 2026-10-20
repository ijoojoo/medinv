<script setup lang="ts">
import {
  commonProfileFields,
  drugProfileFields,
  deviceProfileFields,
  deviceClasses,
  type ProductProfile,
} from "./productProfile";
defineProps<{ category: string }>();
const model = defineModel<ProductProfile>({ required: true });
</script>
<template>
  <section class="full-width product-profile-fields">
    <details open>
      <summary>
        {{ category === "DRUG" ? "药品专属资料" : "医疗器械专属资料" }}
      </summary>
      <p class="form-note">
        按批准证明、标签和说明书核对填写；不确定的内容可暂留空，不自动推断。
      </p>
      <div class="form-grid">
        <label v-if="category === 'DEVICE'"
          >器械管理类别<select v-model="model.device_class">
            <option
              v-for="(label, key) in deviceClasses"
              :key="key"
              :value="key"
            >
              {{ label }}
            </option>
          </select></label
        >
        <label
          v-for="field in category === 'DRUG'
            ? drugProfileFields
            : deviceProfileFields"
          :key="field.key"
          :class="{ 'full-width': field.wide }"
          >{{ field.label
          }}<input v-model="model[field.key]" :maxlength="field.max"
        /></label>
      </div>
    </details>
    <details>
      <summary>检索与储存资料</summary>
      <div class="form-grid">
        <label
          v-for="field in commonProfileFields"
          :key="field.key"
          :class="{ 'full-width': field.wide }"
          >{{ field.label
          }}<textarea
            v-if="field.key === 'storage_conditions'"
            v-model="model[field.key]"
            :maxlength="field.max"
            rows="2" /><input
            v-else
            v-model="model[field.key]"
            :maxlength="field.max"
        /></label>
      </div>
    </details>
  </section>
</template>
<style scoped>
.product-profile-fields {
  border-top: 1px solid #e5ece5;
  margin-top: 4px;
}
summary {
  font-size: 15px;
  margin: 20px 0 14px;
  font-weight: 600;
  cursor: pointer;
}
</style>
