import { ElMessage, ElMessageBox } from "element-plus";
import {
  getCurrentInstance,
  inject,
  nextTick,
  shallowReactive,
  ref,
  watch,
  type VNode,
} from "vue";
import { confirmDates } from "./dateControls";
import { askCloseChanges } from "./closeChanges";
type Check = () => { dirty: boolean; busy: boolean };
const editors = shallowReactive(
  new Map<Check, { page: string; save: () => Promise<boolean> }>(),
);
export const CHANGE_SCOPE = "medinv-change-scope";
export const revealChanges = ref<{ page: string; token: number } | null>(null);
function formsIn(node: VNode | undefined, result = new Set<HTMLFormElement>()) {
  if (!node) return result;
  if (node.el instanceof HTMLFormElement) result.add(node.el);
  if (node.component) formsIn(node.component.subTree, result);
  if (Array.isArray(node.children))
    for (const child of node.children)
      if (child && typeof child === "object") formsIn(child as VNode, result);
  return result;
}
export function registerCatalogChanges(
  check: Check,
  page = "products",
  save?: () => Promise<boolean>,
) {
  const instance = getCurrentInstance();
  const owner = instance
    ? inject<string | undefined>(CHANGE_SCOPE, undefined) || page
    : page;
  const saveForm = async () => {
    const forms = [...formsIn(instance?.subTree)].filter(
      (f) =>
        f.isConnected &&
        !f.matches("[data-date-query], .business-toolbar") &&
        [
          ...f.querySelectorAll<HTMLButtonElement>('button[type="submit"]'),
        ].some((b) => !b.disabled),
    );
    if (forms.length !== 1) {
      ElMessage.warning("请打开正在编辑的表单完成保存，再关闭页面。");
      return false;
    }
    const form = forms[0]!;
    if (!confirmDates(form) || (!form.noValidate && !form.reportValidity()))
      return false;
    form.requestSubmit();
    await nextTick();
    if (check().busy)
      await new Promise<void>((resolve) => {
        const stop = watch(
          () => check().busy,
          (busy) => {
            if (!busy) {
              clearTimeout(timer);
              stop();
              resolve();
            }
          },
        );
        const timer = setTimeout(() => {
          stop();
          resolve();
        }, 60000);
      });
    return !check().busy && !check().dirty;
  };
  editors.set(check, { page: owner, save: save || saveForm });
  return () => {
    editors.delete(check);
  };
}
export function hasCatalogChanges(page?: string) {
  return [...editors].some(([check, owner]) => {
    if (page && owner.page !== page) return false;
    const s = check();
    return s.dirty || s.busy;
  });
}
export async function guardCatalogLeave(page?: string) {
  if (
    [...editors].some(
      ([check, owner]) => (!page || owner.page === page) && check().busy,
    )
  ) {
    await ElMessageBox.alert(
      "正在保存，请等待完成后再关闭页面或切换主体。",
      "正在保存",
    );
    return false;
  }
  if (!hasCatalogChanges(page)) return true;
  const choice = await askCloseChanges(
    "当前页面有尚未保存的修改，请选择保存、放弃或继续编辑。",
  );
  if (choice === "cancel") return false;
  if (choice === "discard") return true;
  const pending = [...editors].filter(
    ([check, owner]) => (!page || owner.page === page) && check().dirty,
  );
  for (const [check, owner] of pending.reverse())
    if (check().dirty && !(await owner.save())) {
      revealChanges.value = { page: owner.page, token: Date.now() };
      return false;
    }
  return !hasCatalogChanges(page);
}
