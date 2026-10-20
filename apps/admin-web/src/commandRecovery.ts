import { computed, ref, watch } from "vue";
import { ApiError, request, session } from "./api";

interface Submission {
  version: 1;
  principal: string;
  tenant: string;
  path: string;
  body: string;
  key: string;
  created: number;
}
const prefix = "medinv.submission.v1:";
const maximumAge = 24 * 60 * 60 * 1000;
export const unresolved = ref<Submission | null>(null);
export const recoveryStorageError = ref("");
const running = ref(new Set<string>());
const scope = () =>
  session.value
    ? `${session.value.principal_id}:${session.value.tenant_id}`
    : "";
const recordScope = (r: Submission) => `${r.principal}:${r.tenant}`;
export const recoveryBusy = computed(() => running.value.has(scope()));

function validPath(path: string) {
  const url = new URL(path, location.origin);
  return (
    path.startsWith("/api/v1/") &&
    url.origin === location.origin &&
    url.pathname.startsWith("/api/v1/") &&
    !url.hash
  );
}
function load() {
  unresolved.value = null;
  recoveryStorageError.value = "";
  if (!scope()) return;
  try {
    const raw = sessionStorage.getItem(prefix + scope());
    if (!raw) return;
    const r = JSON.parse(raw) as Submission;
    if (
      r.version !== 1 ||
      recordScope(r) !== scope() ||
      typeof r.path !== "string" ||
      !validPath(r.path) ||
      typeof r.body !== "string" ||
      r.body.length > 1024 * 1024 ||
      !/^[0-9a-f-]{36}$/.test(r.key) ||
      !Number.isFinite(r.created)
    )
      throw new Error();
    const body: unknown = JSON.parse(r.body);
    if (!body || typeof body !== "object" || Array.isArray(body))
      throw new Error();
    unresolved.value = r;
  } catch {
    recoveryStorageError.value =
      "无法读取本页的提交记录，请先人工核对原单，再结束确认。";
  }
}
watch(scope, load, { immediate: true, flush: "sync" });
function save(r: Submission) {
  try {
    sessionStorage.setItem(prefix + recordScope(r), JSON.stringify(r));
  } catch {
    throw new Error(
      "本页无法保存提交恢复记录，本次未发送。请检查浏览器存储后重试。",
    );
  }
  if (scope() === recordScope(r)) unresolved.value = r;
}
function remove(r: Submission) {
  // Removal failure keeps the original key recoverable, even after a confirmed response.
  sessionStorage.removeItem(prefix + recordScope(r));
  if (scope() === recordScope(r)) unresolved.value = null;
}
function isCurrent(r: Submission) {
  return recordScope(r) === scope();
}
async function send<T>(r: Submission, recovery: boolean): Promise<T> {
  if (!isCurrent(r))
    throw new Error("企业或账号已变化，请回到原企业核对提交。");
  if (running.value.has(scope())) throw new Error("原提交正在处理中，请稍候。");
  if (Date.now() - r.created > maximumAge || r.created > Date.now() + 60000)
    throw new Error(
      "本次提交已超过可自动恢复时间，请到原业务页面人工核对后结束确认。",
    );
  running.value.add(recordScope(r));
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const data = await request<T>(r.path, {
      method: "POST",
      body: r.body,
      signal: controller.signal,
      headers: { "Idempotency-Key": r.key },
    });
    if (data == null) throw new Error("提交结果不完整");
    remove(r);
    return data;
  } catch (error) {
    // A definite first-attempt rejection is safe to correct. Once a response has
    // been lost, a later auth/version error cannot prove the first attempt failed.
    if (
      !recovery &&
      error instanceof ApiError &&
      error.code !== "REQUEST_FAILED" &&
      error.status !== 408 &&
      error.status >= 400 &&
      error.status < 500
    ) {
      remove(r);
      throw error;
    }
    throw new Error(
      "原提交结果尚未确认，请使用页面上方的“继续原提交”，或到原业务页面核对。不要重复收付款或重新制单。",
    );
  } finally {
    clearTimeout(timer);
    running.value.delete(recordScope(r));
  }
}
export async function submitCommand<T>(
  path: string,
  payload: unknown,
): Promise<T> {
  if (!session.value) throw new Error("请先登录");
  if (recoveryStorageError.value) throw new Error(recoveryStorageError.value);
  const body = JSON.stringify(payload);
  if (!body || body.length > 1024 * 1024 || !validPath(path))
    throw new Error("提交内容不符合要求");
  const prior = unresolved.value;
  if (prior) {
    if (prior.path !== path || prior.body !== body)
      throw new Error(
        "本企业还有一笔结果待确认的提交。请先继续原提交或核对原单，再办理新的操作。",
      );
    return send<T>(prior, true);
  }
  const r: Submission = {
    version: 1,
    principal: session.value.principal_id,
    tenant: session.value.tenant_id,
    path,
    body,
    key: crypto.randomUUID(),
    created: Date.now(),
  };
  save(r); // Persist before dispatch, so reloads can repeat the original immutable command.
  return send<T>(r, false);
}
export async function resumeSubmission(): Promise<{
  result: unknown;
  path: string;
}> {
  const r = unresolved.value;
  if (!r) throw new Error("没有待确认提交");
  const result = await send<unknown>(r, true);
  return { result, path: r.path };
}
export function acknowledgeSubmission() {
  if (!scope() || recoveryBusy.value) return;
  sessionStorage.removeItem(prefix + scope());
  load();
}
export function submissionArea(path: string) {
  if (
    /^\/api\/v1\/(purchase-orders|receipts|receipt-postings)(\/|\?|$)/.test(
      path,
    )
  )
    return "采购业务";
  if (
    /^\/api\/v1\/(sales-orders|shipments|customer-credit)(\/|\?|$)/.test(path)
  )
    return "批发销售";
  if (path.startsWith("/api/v1/retail/")) return "零售业务";
  if (path.startsWith("/api/v1/settlements/")) return "往来结算";
  if (path.startsWith("/api/v1/catalog/")) return "基础资料";
  return "库存及业务操作";
}
