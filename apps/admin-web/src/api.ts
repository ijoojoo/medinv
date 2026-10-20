import { ref } from "vue";
export type Named = { id: string; name: string };
export interface Session {
  principal_id: string;
  display_name: string;
  tenant_id: string;
  tenant_name: string;
  context_generation: string;
  permission_version: string;
  permissions: string[];
  role_codes: string[];
  csrf_token: string;
  refresh_needed: boolean;
  tenants: Named[];
  entities: Named[];
  expires_at: string;
}
export const session = ref<Session | null>(null);
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const current = session.value;
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(current
        ? {
            "X-CSRF-Token": current.csrf_token,
            "X-Context-Generation": current.context_generation,
          }
        : {}),
      ...options.headers,
    },
  });
  if (response.status === 204) return undefined as T;
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && session.value === current)
      session.value = null;
    throw new ApiError(
      response.status,
      body?.error?.code || "REQUEST_FAILED",
      body?.error?.message || "请求失败，请稍后重试",
    );
  }
  if (!body || typeof body !== "object" || !("data" in body))
    throw new Error("服务返回内容不完整，请核对原业务结果");
  return body.data as T;
}
let pendingRefresh: Promise<void> | undefined;
export async function loadSession() {
  session.value = await request<Session>("/auth/session");
  if (session.value.refresh_needed) await refreshSession();
}
export async function refreshSession() {
  if (!pendingRefresh) {
    const current = session.value;
    pendingRefresh = request<Session>("/auth/refresh", { method: "POST" })
      .then((value) => {
        if (session.value === current) session.value = value;
      })
      .finally(() => {
        pendingRefresh = undefined;
      });
  }
  await pendingRefresh;
}
