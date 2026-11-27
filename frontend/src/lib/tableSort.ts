export type TableValue = string | number | null | undefined;
export type TableSortDirection = "ascending" | "descending";

const textOrder = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function sortTableRows<T>(
  rows: readonly T[],
  value: (row: T) => TableValue,
  direction: TableSortDirection,
): T[] {
  return [...rows].sort((left, right) => {
    const a = value(left);
    const b = value(right);
    if (a == null) return b == null ? 0 : 1;
    if (b == null) return -1;
    const result = typeof a === "number" && typeof b === "number"
      ? a - b
      : textOrder.compare(String(a), String(b));
    return direction === "ascending" ? result : -result;
  });
}
