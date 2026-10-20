import { decimalMicros, entryQuantity } from "./documentEntry";

export interface ReceiptSource {
  id: string;
  remaining_qty: string;
  qty_scale: number;
  base_unit: string;
}
export interface ReceiptAmount {
  order_line_id: string;
  qty: string;
}
export interface InspectionAmount {
  pending: string;
  qualified_qty: string;
  rejected_qty: string;
  qty_scale: number;
  base_unit: string;
}
export function unallocatedReceipt(
  id: string,
  sources: ReceiptSource[],
  lines: ReceiptAmount[],
  except = -1,
) {
  const source = sources.find((s) => s.id === id);
  let available = decimalMicros(source?.remaining_qty || "");
  if (available === null) return "";
  for (const [i, line] of lines.entries()) {
    if (i === except || line.order_line_id !== id) continue;
    const qty = decimalMicros(line.qty);
    if (qty === null) return "";
    available -= qty;
  }
  if (available <= 0n) return "0";
  return `${available / 1000000n}.${(available % 1000000n).toString().padStart(6, "0")}`.replace(
    /\.?0+$/,
    "",
  );
}
export function receiptAmountIssue(
  sources: ReceiptSource[],
  lines: ReceiptAmount[],
) {
  const totals = new Map<string, bigint>();
  for (const [index, line] of lines.entries()) {
    const source = sources.find((s) => s.id === line.order_line_id);
    if (!source) return { index, message: "请选择原订单中的待收商品" };
    const qty = decimalMicros(line.qty);
    if (qty === 0n) continue; // Explicitly left for a later arrival, never submitted.
    const checked = entryQuantity(line.qty, source.qty_scale, source.base_unit);
    if (checked.message) return { index, message: checked.message };
    const total = (totals.get(source.id) || 0n) + qty!;
    if (total > (decimalMicros(source.remaining_qty) ?? 0n))
      return {
        index,
        message: "同一订单行的到货数量合计超过待收余量，请核对拆批数量",
      };
    totals.set(source.id, total);
  }
  if (!totals.size)
    return { index: 0, message: "请至少填写一行大于零的到货数量" };
  return null;
}
export function inspectionAmountIssue(lines: InspectionAmount[]) {
  let total = 0n;
  for (const [index, line] of lines.entries()) {
    const q = decimalMicros(line.qualified_qty),
      r = decimalMicros(line.rejected_qty);
    if (q === null || r === null)
      return { index, message: "合格、拒收数量须为非负数，最多六位小数" };
    for (const raw of [line.qualified_qty, line.rejected_qty]) {
      if (decimalMicros(raw) === 0n) continue;
      const checked = entryQuantity(raw, line.qty_scale, line.base_unit);
      if (checked.message) return { index, message: checked.message };
    }
    if (q + r > (decimalMicros(line.pending) ?? 0n))
      return { index, message: "合格数量与拒收数量之和超过本行待验数量" };
    total += q + r;
  }
  return total
    ? null
    : { index: 0, message: "请至少填写一行本次已完成验收的数量" };
}
export function inspectionDictionary(
  lines: Pick<InspectionAmount, "qualified_qty" | "rejected_qty">[],
) {
  const qualified = lines.some(
    (l) => (decimalMicros(l.qualified_qty) || 0n) > 0n,
  );
  const rejected = lines.some(
    (l) => (decimalMicros(l.rejected_qty) || 0n) > 0n,
  );
  return rejected
    ? qualified
      ? "inspection_mixed"
      : "inspection_rejected"
    : "inspection_qualified";
}
