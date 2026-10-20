<script setup lang="ts">
import { computed } from "vue";
import { effectiveFields, type ProductFieldRule } from "./productFieldRules";
import {
  commonProfileFields,
  drugProfileFields,
  deviceProfileFields,
  deviceClasses,
  qualityProfileKeys,
  type ProductProfile,
} from "./productProfile";
const props = defineProps<{
  category: string;
  profile: ProductProfile;
  qualificationOnly?: boolean;
  section?: "basic" | "quality";
  policyFields?: ProductFieldRule[];
  contextValues?: Record<string, string>;
}>();
const fields = computed(() =>
  [
    ...(props.category === "DRUG" ? drugProfileFields : deviceProfileFields),
    ...commonProfileFields,
  ].filter(
    (f) =>
      (!props.qualificationOnly || f.key !== "search_alias") &&
      (!props.section ||
        qualityProfileKeys.has(f.key) === (props.section === "quality")),
  ),
);
const presented = computed(() =>
  props.policyFields
    ? effectiveFields(props.policyFields, {
        ...props.contextValues,
        ...props.profile,
      })
        .filter(
          (f) =>
            f.key in props.profile &&
            f.shown &&
            (!props.qualificationOnly || f.review_policy !== "DIRECT_AUDIT") &&
            (!props.section || f.card === props.section),
        )
        .map((f) => ({
          key: f.key,
          label: f.label,
          value:
            f.options[props.profile[f.key as keyof ProductProfile]] ||
            props.profile[f.key as keyof ProductProfile],
        }))
    : fields.value.map((f) => ({ ...f, value: props.profile[f.key] })),
);
</script>
<template>
  <section class="product-profile-details">
    <h3>{{ section === "quality" ? "质量属性" : "商品属性" }}</h3>
    <dl class="facts">
      <template
        v-if="!policyFields && category === 'DEVICE' && section !== 'basic'"
        ><dt>器械管理类别</dt>
        <dd>{{ deviceClasses[profile.device_class] }}</dd></template
      >
      <template v-for="field in presented" :key="field.key"
        ><dt>{{ field.label }}</dt>
        <dd>{{ field.value || "未填写" }}</dd></template
      >
    </dl>
  </section>
</template>
