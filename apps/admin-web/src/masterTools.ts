import { ElMessage } from "element-plus";

export interface MasterTool {
  key: string;
  label: string;
  short?: string;
  icon: string;
  group: "资料维护" | "关联查阅" | "辅助操作";
  disabled?: string;
}

export async function copyMasterCode(code: unknown) {
  if (!code) return;
  try {
    await navigator.clipboard.writeText(String(code));
    ElMessage.success("编码已复制");
  } catch {
    ElMessage.error("未能访问剪贴板，请在资料中选中编码复制");
  }
}

export const coreToolKeys = new Set([
  "new",
  "revise",
  "review",
  "location",
  "rename-location",
  "search",
]);
export const extensionTools = (tools: MasterTool[]) =>
  tools.filter((t) => !coreToolKeys.has(t.key));
