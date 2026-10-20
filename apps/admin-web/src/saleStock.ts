export interface SaleStockItem {
  sku_id: string;
  name: string;
  code: string;
  base_unit: string;
  sale_eligible: boolean;
  available_qty: string;
}
export interface SaleStockSnapshot {
  items: SaleStockItem[];
  data_as_of: string;
  warehouse_id: string;
  minimum_shelf_days: number;
}
export interface UnallocatedLine {
  id: string;
  sku_id: string;
  remaining_qty: string;
}

function scaled(value: string): bigint {
  if (!/^\d{1,18}(\.\d{1,6})?$/.test(value))
    throw new Error("库存数量格式异常，请刷新后重试");
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole!) * 1000000n + BigInt(fraction.padEnd(6, "0"));
}

// Allocate the shared SKU quantity once, even when an order repeats that SKU.
// All inputs and outputs are base units; no binary floating point or rounding.
export function suggestShipment(
  lines: UnallocatedLine[],
  items: SaleStockItem[],
): { order_line_id: string; qty: string }[] {
  const remaining = new Map(
    items.map((item) => [
      item.sku_id,
      item.sale_eligible ? scaled(item.available_qty) : 0n,
    ]),
  );
  return lines.map((line) => {
    const available = remaining.get(line.sku_id) ?? 0n;
    const needed = scaled(line.remaining_qty);
    const take = available < needed ? available : needed;
    remaining.set(line.sku_id, available - take);
    return {
      order_line_id: line.id,
      qty: `${take / 1000000n}.${(take % 1000000n).toString().padStart(6, "0")}`,
    };
  });
}
