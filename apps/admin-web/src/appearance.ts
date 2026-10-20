import { ref, watch } from "vue";

export const themes = [
  { value: "blue", label: "清朗蓝" },
  { value: "teal", label: "青碧" },
  { value: "violet", label: "雅紫" },
] as const;
type Appearance = {
  theme: (typeof themes)[number]["value"];
  density: "compact" | "comfortable";
  layout: "horizontal" | "sidebar";
  sidebarCollapsed: boolean;
};
const storageKey = "medinv.ui.appearance.v1";
function restore(): Appearance {
  const defaults: Appearance = {
    theme: "teal",
    density: "compact",
    layout: "horizontal",
    sidebarCollapsed: false,
  };
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    if (themes.some((t) => t.value === saved?.theme))
      defaults.theme = saved.theme;
    if (saved?.density === "comfortable") defaults.density = "comfortable";
    if (saved?.layout === "sidebar") defaults.layout = "sidebar";
    defaults.sidebarCollapsed = saved?.sidebarCollapsed === true;
  } catch {
    // Appearance is optional; unavailable storage must not prevent business entry.
  }
  return defaults;
}
export const appearance = ref(restore());
watch(
  appearance,
  (value) => {
    if (typeof document === "undefined") return;
    document.documentElement.dataset.theme = value.theme;
    document.documentElement.dataset.entryDensity = value.density;
    document.documentElement.dataset.layout = value.layout;
    document.documentElement.dataset.sidebarCollapsed = String(
      value.sidebarCollapsed,
    );
    try {
      localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      /* Local preference only. */
    }
  },
  { deep: true, immediate: true },
);
