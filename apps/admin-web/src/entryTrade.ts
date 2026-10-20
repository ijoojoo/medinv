import { decimalMicros } from "./documentEntry";

export function microsText(value: bigint): string {
  const fraction = String(value % 1000000n)
    .padStart(6, "0")
    .replace(/0+$/, "");
  return `${value / 1000000n}${fraction ? "." + fraction : ""}`;
}

// Reference prices are per base unit. Keep the packaging ratio exact until
// the final, explicit six-decimal rounding; never use floating point money.
export function packagingReferencePrice(
  price: string | null | undefined,
  numerator = "1",
  denominator = "1",
): { value: string; rounded: boolean } | null {
  if (
    price == null ||
    !/^[1-9][0-9]{0,8}$/.test(numerator) ||
    !/^[1-9][0-9]{0,8}$/.test(denominator)
  )
    return null;
  const base = decimalMicros(price);
  if (base === null) return null;
  const scaled = base * BigInt(numerator),
    divisor = BigInt(denominator);
  const result = (scaled * 2n + divisor) / (divisor * 2n);
  // Same upper bound as document-entry's decimal validation.
  if (result > 99999999999999999999n) return null;
  return { value: microsText(result), rounded: scaled % divisor !== 0n };
}
