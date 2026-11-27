import { useId, useState, type Key, type ReactNode } from "react";
import { sortTableRows, type TableSortDirection, type TableValue } from "../lib/tableSort";

export interface DataColumn<T> {
  key: string;
  label: string;
  value?: (row: T) => TableValue;
  cell?: (row: T) => ReactNode;
  className?: string;
  filter?: boolean;
  required?: boolean;
}

function savedHidden(storageKey: string): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
    return Array.isArray(saved) ? saved.filter((key): key is string => typeof key === "string") : [];
  } catch {
    return [];
  }
}

export function DataTable<T>({
  rows, columns, rowKey, storageId, label, searchLabel, searchText, loading, error,
  empty = "No rows to display.", noMatch = "No rows match these filters.", className, exportCsv,
}: {
  rows: readonly T[];
  columns: readonly DataColumn<T>[];
  rowKey: (row: T) => Key;
  storageId: string;
  label: string;
  searchLabel?: string;
  searchText?: (row: T) => string;
  loading?: boolean;
  error?: string | null;
  empty?: string;
  noMatch?: string;
  className?: string;
  exportCsv?: (visibleRows: readonly T[], visibleColumns: readonly DataColumn<T>[]) => void;
}) {
  const searchId = useId();
  const storageKey = `signal-bot:table-columns:${storageId}`;
  const [hidden, setHidden] = useState(() => savedHidden(storageKey));
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Partial<Record<string, string>>>({});
  const [sort, setSort] = useState<{ column: string; direction: TableSortDirection } | null>(null);
  const selected = columns.filter((column) => column.required || !hidden.includes(column.key));
  const visible = selected.length ? selected : columns.slice(0, 1);
  const needle = query.trim().toLocaleLowerCase();

  const filtered = rows.filter((row) => {
    const text = searchText?.(row) ?? columns.map((column) => column.value?.(row)).join(" ");
    return (!needle || text.toLocaleLowerCase().includes(needle)) &&
      visible.every((column) => !filters[column.key] || String(column.value?.(row) ?? "") === filters[column.key]);
  });
  const sortColumn = visible.find((column) => column.key === sort?.column);
  const shown = sort && sortColumn?.value ? sortTableRows(filtered, sortColumn.value, sort.direction) : filtered;

  function toggleColumn(key: string) {
    const next = hidden.includes(key) ? hidden.filter((item) => item !== key) : [...hidden, key];
    setHidden(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      // Column visibility is still applied for the current session.
    }
  }

  function cycleSort(column: string) {
    if (sort?.column !== column) setSort({ column, direction: "ascending" });
    else if (sort.direction === "ascending") setSort({ column, direction: "descending" });
    else setSort(null);
  }

  return <div aria-busy={loading}>
    <div className="data-table-toolbar">
      <label htmlFor={searchId}>{searchLabel ?? `Search ${label}`}</label>
      <input id={searchId} type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
      {visible.filter((column) => column.filter && column.value).map((column) => (
        <label key={column.key}>{column.label}
          <select aria-label={`Filter by ${column.label}`} value={filters[column.key] ?? ""}
            onChange={(event) => setFilters({ ...filters, [column.key]: event.target.value })}>
            <option value="">All</option>
            {[...new Set(rows.map((row) => String(column.value?.(row) ?? "")).filter(Boolean))].sort().map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      ))}
      <details className="data-table-columns">
        <summary>Columns</summary>
        {columns.filter((column) => !column.required).map((column) => (
          <label key={column.key}>
            <input type="checkbox" checked={visible.includes(column)}
              disabled={visible.length === 1 && visible.includes(column)}
              onChange={() => toggleColumn(column.key)} />
            Show {column.label}
          </label>
        ))}
      </details>
      {exportCsv && <button type="button" className="button button-secondary" onClick={() => exportCsv(shown, visible)} disabled={!shown.length}>Export CSV</button>}
    </div>
    {error && <p className="api-error" role="alert">{error}</p>}
    {shown.length ? <div className="table-scroll data-table-scroll" tabIndex={0} role="region" aria-label={label}>
      <table className={className} aria-label={label}>
        <thead><tr>{visible.map((column) => (
          <th key={column.key} scope="col" aria-sort={column.value ? sort?.column === column.key ? sort.direction : "none" : undefined}>
            {column.value
              ? <button type="button" className="data-table-sort" onClick={() => cycleSort(column.key)}>
                  {column.label}
                  {sort?.column === column.key && <span aria-hidden="true">{sort.direction === "ascending" ? " ↑" : " ↓"}</span>}
                </button>
              : column.label}
          </th>
        ))}</tr></thead>
        <tbody>{shown.map((row) => <tr key={rowKey(row)}>{visible.map((column) => (
          <td key={column.key} className={column.className}>{column.cell ? column.cell(row) : column.value?.(row) ?? "—"}</td>
        ))}</tr>)}</tbody>
      </table>
    </div> : !error && <p className="empty-state" role={loading ? "status" : undefined}>
      {loading ? `Loading ${label}…` : rows.length ? noMatch : empty}
    </p>}
  </div>;
}
