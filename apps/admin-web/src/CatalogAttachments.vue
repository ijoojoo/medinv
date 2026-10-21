<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { ApiError, request, session } from "./api";
import { can } from "./business";
import { registerCatalogChanges } from "./catalogChanges";
import AttachmentLink from "./AttachmentLink.vue";
interface Attachment {
  id: string;
  root_id: string;
  previous_id: string | null;
  version: number;
  filename: string;
  media_type: string;
  byte_size: number;
  sha256: string;
  reason: string;
  created_at: string;
}
const props = defineProps<{
  area: "products" | "parties";
  readonly?: boolean;
  parent: string;
  modelValue: string | null;
  selectable: boolean;
  disabled: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [id: string | null];
  failure: [error: unknown];
  editing: [value: boolean];
}>();
const root = computed(
  () => `/api/v1/catalog/${props.area}/${props.parent}/attachments`,
);
const items = ref<Attachment[]>([]),
  busy = ref(false),
  loading = ref(false);
const file = ref<File | null>(null),
  input = ref<HTMLInputElement | null>(null);
const reason = ref(""),
  previous = ref(""),
  loaded = ref(false);
const selected = computed(() =>
  items.value.find((r) => r.id === props.modelValue),
);
const latest = computed(() =>
  items.value.filter((r) => !items.value.some((n) => n.previous_id === r.id)),
);
const dirty = computed(() => !!file.value || !!reason.value);
const contextKey = () =>
  `${session.value?.principal_id}:${session.value?.tenant_id}:${session.value?.context_generation}`;
let active = true;
onUnmounted(() => {
  active = false;
});
onUnmounted(
  registerCatalogChanges(
    () => ({ dirty: dirty.value, busy: busy.value }),
    props.area,
  ),
);
watch(
  () => dirty.value || busy.value,
  (value) => emit("editing", value),
);
async function load() {
  loading.value = true;
  const scope = contextKey();
  try {
    const result = await request<{ items: Attachment[] }>(root.value);
    if (active && scope === contextKey()) {
      items.value = result.items;
      loaded.value = true;
    }
  } catch (error) {
    if (active && scope === contextKey()) emit("failure", error);
  } finally {
    loading.value = false;
  }
}
function choose(event: Event) {
  const selectedFile = (event.target as HTMLInputElement).files?.[0] || null;
  if (
    selectedFile &&
    (!selectedFile.size || selectedFile.size > 5 * 1024 * 1024)
  ) {
    ElMessage.error("请选择不超过 5 MiB 的非空文件");
    (event.target as HTMLInputElement).value = "";
    file.value = null;
    return;
  }
  file.value = selectedFile;
}
function reset() {
  file.value = null;
  reason.value = "";
  previous.value = "";
  if (input.value) input.value.value = "";
}
async function hex(bytes: ArrayBuffer) {
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
}
async function upload() {
  if (!file.value || !reason.value.trim() || busy.value || props.disabled)
    return;
  busy.value = true;
  const scope = contextKey(),
    path = root.value,
    chosen = file.value;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  const metadata = {
    filename: chosen.name,
    reason: reason.value.trim(),
    ...(previous.value ? { previous_id: previous.value } : {}),
  };
  try {
    const bytes = await chosen.arrayBuffer();
    const digest = await hex(bytes);
    // Stable retry identity survives reselecting the same file; file bytes are never put in browser storage.
    const identity = JSON.stringify([
      session.value?.principal_id,
      session.value?.tenant_id,
      path,
      metadata,
      digest,
    ]);
    const key =
      "attachment-" + (await hex(new TextEncoder().encode(identity).buffer));
    if (!active || scope !== contextKey()) return;
    const result = await request<Attachment>(
      `${path}?${new URLSearchParams(metadata)}`,
      {
        method: "POST",
        body: bytes,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/octet-stream",
          "Idempotency-Key": key,
        },
      },
    );
    if (!active || scope !== contextKey()) return;
    reset();
    await load();
    ElMessage.success(
      `附件第 ${result.version} 版已保存；请在证照编辑时选择关联版本`,
    );
  } catch (error) {
    if (active && scope === contextKey())
      emit(
        "failure",
        error instanceof ApiError
          ? error
          : new Error(
              "附件上传结果暂未确认，请保留相同文件和说明重试，或刷新附件列表核对。",
            ),
      );
  } finally {
    clearTimeout(timer);
    busy.value = false;
  }
}
onMounted(load);
</script>
<template>
  <section class="attachments" aria-label="私有附件">
    <div class="heading">
      <h3>原件附件</h3>
      <el-button :disabled="busy || loading" @click="load">刷新附件</el-button>
    </div>
    <p class="form-note">
      PDF、PNG、JPEG，单份不超过 5
      MiB。替换保留历史；上传后保存证照关联，随主体资料一起审核。
    </p>
    <p v-if="selected">
      当前证照关联：{{ selected.filename }} · 第 {{ selected.version }} 版
    </p>
    <p v-else-if="!readonly" class="form-note">
      当前证照未关联附件。可在新增或修订证照时选择已上传的版本。
    </p>
    <label v-if="selectable" class="selection"
      >证照关联附件版本
      <select
        :value="modelValue || ''"
        :disabled="disabled || busy || !loaded"
        @change="
          emit(
            'update:modelValue',
            ($event.target as HTMLSelectElement).value || null,
          )
        "
      >
        <option value="">不关联附件</option>
        <option v-for="row in items" :key="row.id" :value="row.id">
          {{ row.filename }} · 第 {{ row.version }} 版
        </option>
      </select>
    </label>
    <form
      v-if="!readonly && can('catalog.write')"
      class="upload"
      @submit.prevent="upload"
    >
      <fieldset :disabled="busy || disabled">
        <label
          >选择附件<input
            ref="input"
            type="file"
            accept=".pdf,.png,.jpg,.jpeg"
            @change="choose"
        /></label>
        <label
          >上传方式<select v-model="previous">
            <option value="">新附件</option>
            <option v-for="row in latest" :key="row.id" :value="row.id">
              替换 {{ row.filename }} · 第 {{ row.version }} 版
            </option>
          </select></label
        >
        <label
          >附件上传说明<input
            v-model="reason"
            maxlength="500"
            placeholder="如补充营业执照原件"
        /></label>
      </fieldset>
      <div class="heading">
        <span class="form-note"
          >含历史最多 100 个版本、100 MiB；上传失败可原样重试。</span
        ><span>
          <el-button :disabled="busy || disabled" @click="reset"
            >清空待上传</el-button
          >
          <el-button
            native-type="submit"
            type="primary"
            :loading="busy"
            :disabled="disabled || !file || !reason.trim()"
            >上传附件</el-button
          >
        </span>
      </div>
    </form>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>文件与版本</th>
            <th>大小 / 上传时间</th>
            <th>上传说明</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.id" :data-attachment-id="row.id">
            <td>
              {{ row.filename }} · 第 {{ row.version }} 版
              <strong v-if="row.id === modelValue">（当前关联）</strong
              ><small>{{
                latest.some((r) => r.id === row.id) ? "最新版本" : "历史版本"
              }}</small>
            </td>
            <td>
              {{ (row.byte_size / 1024).toFixed(1) }} KiB<small>{{
                new Date(row.created_at).toLocaleString("zh-CN")
              }}</small>
            </td>
            <td>{{ row.reason }}</td>
            <td>
              <AttachmentLink :area="area" :parent="parent" :id="row.id" />
            </td>
          </tr>
          <tr v-if="loaded && !items.length">
            <td colspan="4">尚未上传附件</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
<style scoped>
.attachments {
  margin-top: 24px;
  border-top: 1px solid var(--ui-border);
  padding-top: 16px;
}
.heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.upload {
  padding: 16px;
  background: var(--ui-surface-soft, #f6f8fa);
  margin: 16px 0;
  border-radius: 8px;
}
fieldset {
  border: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
input,
select {
  min-width: 0;
  padding: 8px;
}
.selection {
  max-width: 600px;
  margin: 12px 0;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid var(--ui-border);
  overflow-wrap: anywhere;
}
small {
  display: block;
  color: var(--ui-muted);
  margin-top: 4px;
}
</style>
