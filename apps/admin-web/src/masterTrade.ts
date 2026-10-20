import type { MasterValues } from "./productMaster";
export interface BaseBarcode {
  id: string;
  version: number | string;
  external_code: string;
  packaging_id: string | null;
}
export interface InlinePrices {
  purchase_price: string;
  wholesale_price: string;
  retail_price: string;
  note: string;
}
export const priceLabels = {
  purchase_price: "参考采购价",
  wholesale_price: "参考批发价",
  retail_price: "建议零售价",
};
export function baseBarcodes(values: MasterValues): BaseBarcode[] {
  return ((values.barcodes || []) as BaseBarcode[]).filter(
    (c) => !c.packaging_id,
  );
}
export function inlinePrices(values: MasterValues): InlinePrices {
  const row = (
    values.reference_prices as Record<string, unknown>[] | undefined
  )?.[0];
  return {
    purchase_price: String(row?.purchase_price ?? ""),
    wholesale_price: String(row?.wholesale_price ?? ""),
    retail_price: String(row?.retail_price ?? ""),
    note: String(row?.note ?? ""),
  };
}
export function tradeChanges(
  original: MasterValues,
  barcode: string,
  prices: InlinePrices,
  reason: string,
) {
  const base = baseBarcodes(original);
  const priceRow = (
    original.reference_prices as { version: number }[] | undefined
  )?.[0];
  return {
    ...(barcode !== (base[0]?.external_code || "")
      ? {
          basic_unit_barcode: {
            expected_entries: base.map((c) => ({
              id: c.id,
              version: String(c.version),
            })),
            value: barcode || null,
          },
        }
      : {}),
    ...(JSON.stringify(prices) !== JSON.stringify(inlinePrices(original))
      ? {
          reference_prices: {
            expected_version: String(priceRow?.version || 0),
            ...prices,
            ...Object.fromEntries(
              Object.keys(priceLabels).map((k) => [
                k,
                prices[k as keyof typeof priceLabels] || null,
              ]),
            ),
            reason,
          },
        }
      : {}),
  };
}
