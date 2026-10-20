export interface DocumentSearch {
  search: string;
  state: string;
  date_from: string;
  date_to: string;
  warehouse_id?: string;
  party_id?: string;
  kind?: string;
}
export const emptyDocumentSearch = (): DocumentSearch => ({
  search: "",
  state: "",
  date_from: "",
  date_to: "",
  party_id: "",
  warehouse_id: "",
  kind: "",
});
export function documentQuery(
  entity: string,
  offset: number,
  filters: DocumentSearch,
) {
  const params = new URLSearchParams({
    entity_id: entity,
    offset: String(offset),
  });
  for (const [key, value] of Object.entries(filters)) {
    if (typeof value === "string" && value.trim())
      params.set(key, value.trim());
  }
  return params.toString();
}
