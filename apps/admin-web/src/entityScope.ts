import { ref, watch } from "vue";
import { session } from "./api";

export const activeEntity = ref("");
const key = () =>
  `medinv.entity.v1:${session.value?.tenant_id}:${session.value?.principal_id}`;
export function selectEntity(id: string) {
  if (!session.value?.entities.some((e) => e.id === id)) return false;
  activeEntity.value = id;
  try {
    localStorage.setItem(key(), id);
  } catch {
    /* Selection still works. */
  }
  return true;
}
watch(
  session,
  (s) => {
    let saved = "";
    try {
      saved = localStorage.getItem(key()) || "";
    } catch {
      /* Use the first authorized entity. */
    }
    activeEntity.value =
      s?.entities.find((e) => e.id === saved)?.id || s?.entities[0]?.id || "";
  },
  { immediate: true, flush: "sync" },
);
