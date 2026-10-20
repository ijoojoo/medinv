export interface WorkQueueFilters {
  entity_id: string;
  kind: string;
  search: string;
  offset: number;
  priority?: boolean;
  limit?: number;
  input?: string;
}
export type WorkbenchVisit = {
  item: WorkQueueItem;
  index: number;
} & (
  | { view: "operations"; filters: WorkQueueFilters }
  | {
      view: "risk";
      filters: { group: string; search: string; input: string; offset: number };
    }
);
export interface WorkQueueItem {
  kind: string;
  page:
    | "purchasing"
    | "selling"
    | "retail"
    | "returns"
    | "transfers"
    | "counts"
    | "openings"
    | "quality";
  entity_id: string;
  item_id: string;
  target_id: string;
  number: string;
  related_number: string | null;
  party_name: string;
  warehouse_name: string;
  document_time: string;
  reservation_expired: boolean;
}
