<script setup lang="ts">
import { ref, onUnmounted, watch, nextTick } from "vue";
import { ElAutocomplete } from "element-plus";
import { request } from "./api";
import { labels } from "./business";
const props = defineProps<{
  name: string;
  creditCode: string;
  entity: string;
  editing: boolean;
}>();
const emit = defineEmits<{
  "update:name": [value: string];
  "update:creditCode": [value: string];
  organization: [id: string];
  existing: [id: string];
}>();
interface Candidate {
  id: string;
  name: string;
  legal_code: string;
  kind: string;
  value: string;
  existing?: boolean;
  code?: string;
}
const matched = ref("");
const error = ref("");
let epoch = 0;
onUnmounted(() => epoch++);
watch(
  () => props.creditCode,
  () => {
    matched.value = "";
    emit("organization", "");
  },
);
async function suggest(query: string, done: (items: Candidate[]) => void) {
  const token = ++epoch;
  error.value = "";
  if (props.editing || !query.trim()) {
    done([]);
    return;
  }
  try {
    const [organizations, parties] = await Promise.all([
      request<{ items: Candidate[] }>(
        `/api/v1/catalog/master/organizations?search=${encodeURIComponent(query.trim())}`,
      ),
      request<{ items: (Candidate & { credit_code: string })[] }>(
        `/api/v1/catalog/parties?${new URLSearchParams({ entity_id: props.entity, search: query.trim() })}`,
      ),
    ]);
    if (token !== epoch) return;
    done(
      [
        ...parties.items.map((p) => ({
          ...p,
          legal_code: p.credit_code,
          value: p.name,
          existing: true,
        })),
        ...organizations.items
          .filter((o) => o.kind === "ORGANIZATION")
          .map((o) => ({ ...o, value: o.name })),
      ].slice(0, 30),
    );
  } catch {
    if (token !== epoch) return;
    done([]);
    error.value = "候选暂时无法读取，输入已保留；保存时会重新校验。";
  }
}
function input(value: string | number) {
  matched.value = "";
  emit("organization", "");
  emit("update:name", String(value));
}
async function choose(item: Record<string, any>) {
  const candidate = item as Candidate;
  if (candidate.existing) {
    emit("existing", candidate.id);
    return;
  }
  emit("update:name", candidate.name);
  emit("update:creditCode", candidate.legal_code || props.creditCode);
  // The parent assigns credit first; the reference must survive that value update.
  await nextTick();
  emit("organization", candidate.id);
  matched.value = candidate.name;
}
</script>
<template>
  <label class="party-name-field"
    >名称
    <input
      v-if="editing"
      :value="name"
      required
      maxlength="200"
      @input="input(($event.target as HTMLInputElement).value)"
    />
    <ElAutocomplete
      v-else
      :model-value="name"
      :fetch-suggestions="suggest"
      :debounce="180"
      :trigger-on-focus="false"
      aria-label="名称"
      required
      maxlength="200"
      placeholder="输入单位名称或信用代码，匹配已有资料"
      @update:model-value="input"
      @select="choose"
    >
      <template #default="{ item }"
        ><div class="party-candidate">
          <b>{{ item.name }}</b
          ><small
            >{{
              item.existing
                ? `已建档 · ${labels[item.kind]} · ${item.code}`
                : "机构档案 · 可复用身份"
            }}
            · {{ item.legal_code || "信用代码待补充" }}</small
          >
        </div></template
      >
    </ElAutocomplete>
    <small v-if="matched">已匹配：{{ matched }}</small>
    <small v-else-if="!editing">↑↓ 选择、Enter 确认；新机构随资料保存。</small>
    <small v-if="error" role="status">{{ error }}</small>
  </label>
  <label
    >统一社会信用代码<input
      :value="creditCode"
      :disabled="editing"
      required
      maxlength="32"
      @input="
        emit('update:creditCode', ($event.target as HTMLInputElement).value)
      "
  /></label>
</template>
<style scoped>
.party-name-field :deep(.el-autocomplete) {
  width: 100%;
}
.party-name-field :deep(.el-input__wrapper) {
  min-height: 36px;
}
.party-name-field :deep(.el-input__inner) {
  border: 0;
  height: 34px;
  padding: 0;
}
.party-candidate {
  display: grid;
  line-height: 1.5;
  padding: 6px 0;
}
.party-candidate small {
  color: var(--muted);
}
</style>
