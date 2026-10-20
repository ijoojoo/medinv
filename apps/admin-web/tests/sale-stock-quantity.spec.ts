import { test, expect } from "@playwright/test";
import { suggestShipment, type SaleStockItem } from "../src/saleStock";

function stock(qty: string, sku = "a", eligible = true): SaleStockItem {
  return {
    sku_id: sku,
    available_qty: qty,
    sale_eligible: eligible,
    name: "商品",
    code: sku,
    base_unit: "个",
  };
}
test("partial shipment shares stock across repeated SKU lines and caps remaining demand", () => {
  expect(
    suggestShipment(
      [
        { id: "1", sku_id: "a", remaining_qty: "3.000000" },
        { id: "2", sku_id: "a", remaining_qty: "6.000000" },
        { id: "3", sku_id: "b", remaining_qty: "2.000000" },
        { id: "4", sku_id: "c", remaining_qty: "5.000000" },
      ],
      [stock("7"), stock("10", "b")],
    ),
  ).toEqual([
    { order_line_id: "1", qty: "3.000000" },
    { order_line_id: "2", qty: "4.000000" },
    { order_line_id: "3", qty: "2.000000" },
    { order_line_id: "4", qty: "0.000000" },
  ]);
});
test("six decimal and large quantities retain the exact last unit", () => {
  expect(
    suggestShipment(
      [
        { id: "1", sku_id: "a", remaining_qty: "99999999999999.999998" },
        { id: "2", sku_id: "a", remaining_qty: "0.000002" },
      ],
      [stock("99999999999999.999999")],
    ),
  ).toEqual([
    { order_line_id: "1", qty: "99999999999999.999998" },
    { order_line_id: "2", qty: "0.000001" },
  ]);
});
test("unavailable products and fulfilled lines get zero", () => {
  expect(
    suggestShipment(
      [
        { id: "1", sku_id: "a", remaining_qty: "3" },
        { id: "2", sku_id: "b", remaining_qty: "0" },
      ],
      [stock("100", "a", false), stock("100", "b")],
    ).map((r) => r.qty),
  ).toEqual(["0.000000", "0.000000"]);
});
for (const value of ["-1", "1e5", "0.0000001", "NaN", "", "1,000"]) {
  test(`invalid quantity ${JSON.stringify(value)} fails instead of silently filling`, () => {
    expect(() =>
      suggestShipment(
        [{ id: "1", sku_id: "a", remaining_qty: value }],
        [stock("10")],
      ),
    ).toThrow();
  });
}
