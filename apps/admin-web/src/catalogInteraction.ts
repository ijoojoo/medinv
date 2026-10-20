import { nextTick, ref, type Ref } from "vue";

/** A list visit belongs to the mounted workspace, never to another tenant or tab. */
export function useCatalogListReturn(root: Ref<HTMLElement | undefined>) {
  const returnedId = ref("");
  let visit: {
    id: string;
    index: number;
    top: number;
    left: number;
    tableLeft: number[];
  } | null = null;
  const buttons = () => [
    ...(root.value?.querySelectorAll<HTMLButtonElement>(
      "button[data-catalog-record]",
    ) || []),
  ];
  const scrolls = () => [
    ...(root.value?.querySelectorAll<HTMLElement>(
      ".master-table-scroll, .el-scrollbar__wrap",
    ) || []),
  ];
  function capture(id = "") {
    const area = root.value?.closest<HTMLElement>(".main-shell");
    visit = {
      id,
      index: Math.max(
        0,
        buttons().findIndex((b) => b.dataset.catalogRecord === id),
      ),
      top: area?.scrollTop || 0,
      left: area?.scrollLeft || 0,
      tableLeft: scrolls().map((s) => s.scrollLeft),
    };
  }
  function clear() {
    visit = null;
    returnedId.value = "";
  }
  async function restore() {
    await nextTick();
    if (!root.value?.getClientRects().length || !visit) return;
    const area = root.value.closest<HTMLElement>(".main-shell");
    area?.scrollTo({ top: visit.top, left: visit.left, behavior: "instant" });
    scrolls().forEach((s, i) => (s.scrollLeft = visit!.tableLeft[i] || 0));
    const rows = buttons();
    const exact = rows.find((b) => b.dataset.catalogRecord === visit!.id);
    returnedId.value = exact?.dataset.catalogRecord || "";
    const target = visit.id
      ? exact || rows[Math.min(visit.index, rows.length - 1)]
      : null;
    if (target) {
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "nearest", inline: "nearest" });
    } else
      root.value
        .querySelector<HTMLInputElement>("input[data-catalog-search]")
        ?.focus({ preventScroll: true });
  }
  function keys(event: KeyboardEvent) {
    if (
      event.isComposing ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey
    )
      return;
    const rows = buttons();
    const target = event.target as HTMLElement;
    const index = rows.indexOf(target as HTMLButtonElement);
    let next: number;
    if (target.matches("[data-catalog-search]") && event.key === "ArrowDown")
      next = 0;
    else if (index >= 0 && event.key === "ArrowDown")
      next = Math.min(index + 1, rows.length - 1);
    else if (index >= 0 && event.key === "ArrowUp")
      next = Math.max(index - 1, 0);
    else if (index >= 0 && event.key === "Home") next = 0;
    else if (index >= 0 && event.key === "End") next = rows.length - 1;
    else return;
    if (rows[next]) {
      event.preventDefault();
      rows[next]!.focus();
    }
  }
  return { returnedId, capture, clear, restore, keys };
}

/** Bound to the actual editing surface; hidden module tabs cannot intercept it. */
export function catalogSaveKey(event: KeyboardEvent, save: () => unknown) {
  if (
    event.isComposing ||
    event.defaultPrevented ||
    event.altKey ||
    event.shiftKey ||
    !(event.ctrlKey || event.metaKey) ||
    event.key.toLowerCase() !== "s"
  )
    return;
  event.preventDefault();
  event.stopPropagation();
  if (!event.repeat) void save();
}

export function submitCatalogForm(event: KeyboardEvent) {
  catalogSaveKey(event, () => {
    const form = event.currentTarget as HTMLFormElement;
    const submit = form.querySelector<HTMLButtonElement>(
      'button[type="submit"]:not(:disabled)',
    );
    if (submit) form.requestSubmit(submit);
  });
}
