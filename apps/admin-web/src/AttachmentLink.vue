<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { ApiError, session } from "./api";
import { ElMessage } from "element-plus";
const props = defineProps<{
  area: "products" | "parties";
  parent: string;
  id: string;
}>();
const busy = ref(false),
  preview = ref(""),
  filename = ref("");
let active = true;
function clear() {
  if (preview.value) URL.revokeObjectURL(preview.value);
  preview.value = "";
}
onUnmounted(() => {
  active = false;
  clear();
});
async function open() {
  if (busy.value || !session.value) return;
  const context = session.value;
  busy.value = true;
  try {
    const response = await fetch(
      `/api/v1/catalog/${props.area}/${props.parent}/attachments/${props.id}/content`,
      {
        credentials: "same-origin",
        headers: { "X-Context-Generation": context.context_generation },
      },
    );
    if (!response.ok) {
      const body = await response.json();
      throw new ApiError(
        response.status,
        body.error?.code || "REQUEST_FAILED",
        body.error?.message || "附件读取失败",
      );
    }
    const blob = await response.blob();
    if (!active || session.value !== context) return;
    filename.value = decodeURIComponent(
      (response.headers.get("Content-Disposition") || "").split("UTF-8''")[1] ||
        "附件",
    );
    const url = URL.createObjectURL(blob);
    if (["image/png", "image/jpeg"].includes(blob.type)) {
      clear();
      preview.value = url;
    } else {
      const link = document.createElement("a");
      link.href = url;
      link.download = filename.value;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  } catch (error) {
    if (active && session.value === context)
      ElMessage.error(error instanceof Error ? error.message : "附件读取失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <el-button text :loading="busy" @click="open">查看 / 下载附件</el-button>
  <el-dialog
    :model-value="!!preview"
    :title="filename"
    width="min(900px, 90vw)"
    append-to-body
    @close="clear"
  >
    <img
      v-if="preview"
      :src="preview"
      :alt="filename"
      class="attachment-image"
    />
    <template #footer
      ><a :href="preview" :download="filename">下载原文件</a></template
    >
  </el-dialog>
</template>
<style scoped>
.attachment-image {
  display: block;
  max-width: 100%;
  max-height: 70vh;
  margin: auto;
  object-fit: contain;
}
</style>
