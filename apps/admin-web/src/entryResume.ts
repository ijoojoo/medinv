// View position belongs to this browser tab, never to a business draft/version.
export type EntryResume =
  | { area: "header"; field: string }
  | { area: "line"; key: string; index: number; column: string };
const headers = [
  "party",
  "warehouse",
  "title",
  "address",
  "shelf",
  "credit",
  "reason",
];
const columns = ["product", "pack", "qty", "price", "tax"];
export function validEntryResume(value: unknown): value is EntryResume {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (v.area === "header") return headers.includes(String(v.field));
  return (
    v.area === "line" &&
    typeof v.key === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      v.key,
    ) &&
    Number.isInteger(v.index) &&
    Number(v.index) >= 0 &&
    Number(v.index) < 50 &&
    columns.includes(String(v.column))
  );
}
export const entryResumeKey = (
  tenant: string,
  principal: string,
  entity: string,
  draft: string,
) => `medinv.entry-position.v1:${tenant}:${principal}:${entity}:${draft}`;
export function readEntryResume(key: string): EntryResume | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 1000) return;
    const value = JSON.parse(raw);
    const age = Date.now() - value.time;
    if (age >= 0 && age < 86400000 && validEntryResume(value.position))
      return value.position;
    sessionStorage.removeItem(key);
  } catch {
    // Position is optional. A malformed or unavailable cache never blocks editing.
  }
}
export function writeEntryResume(key: string, position: EntryResume) {
  if (!validEntryResume(position)) return;
  try {
    sessionStorage.setItem(key, JSON.stringify({ time: Date.now(), position }));
  } catch {
    // The current component still retains its position when storage is unavailable.
  }
}
