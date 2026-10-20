import { request } from "./api";
import type {
  EntryChannel,
  EntryProduct,
  EntryPackages,
} from "./documentEntry";
import {
  prepareEntryFileRow,
  validateEntryFileRow,
  type EntryFileRow,
} from "./entryFiles";

export async function resolveEntryFile(
  rows: EntryFileRow[],
  entity: string,
  channel: EntryChannel,
  signal: AbortSignal,
) {
  for (const row of rows) prepareEntryFileRow(row);
  const key = (row: EntryFileRow) => `${row.matchBy || "code"}:${row.code}`;
  const products = new Map<string, EntryProduct | undefined>();
  const codes = [
    ...new Map(
      rows.filter((r) => !r.errors.length).map((r) => [key(r), r]),
    ).values(),
  ];
  for (let i = 0; i < codes.length; i += 4) {
    signal.throwIfAborted();
    await Promise.all(
      codes.slice(i, i + 4).map(async (row) => {
        const barcode = row.matchBy === "barcode";
        const result = await request<{ items: EntryProduct[] }>(
          `/api/v1/catalog/entry-products?${new URLSearchParams({ entity_id: entity, channel, search: row.code, match_barcode: String(barcode) })}`,
          { signal },
        );
        const exact = result.items.filter((p) =>
          barcode
            ? p.barcode_match?.barcode === row.code
            : p.code.toLowerCase() === row.code.toLowerCase(),
        );
        products.set(key(row), exact.length === 1 ? exact[0] : undefined);
      }),
    );
  }
  const packs = new Map<string, EntryPackages>();
  const skus = [
    ...new Set(
      rows.flatMap((r) => {
        const p = products.get(key(r));
        return p &&
          !r.errors.length &&
          (r.packageId ||
            (r.unit && r.unit !== p.base_unit) ||
            (r.matchBy === "barcode" && p.barcode_match?.packaging_id))
          ? [p.id]
          : [];
      }),
    ),
  ];
  for (let i = 0; i < skus.length; i += 4) {
    signal.throwIfAborted();
    await Promise.all(
      skus.slice(i, i + 4).map(async (sku) => {
        packs.set(
          sku,
          await request<EntryPackages>(
            `/api/v1/catalog/skus/${sku}/packaging?approved_only=true`,
            { signal },
          ),
        );
      }),
    );
  }
  signal.throwIfAborted();
  for (const row of rows) {
    const product = products.get(key(row));
    validateEntryFileRow(
      row,
      product,
      product ? packs.get(product.id) : undefined,
    );
  }
  return rows;
}
