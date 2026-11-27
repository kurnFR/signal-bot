import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DataTable, type DataColumn } from "./DataTable";

const rows = [
  { id: 1, name: "Beta", volume: 10, market: "spot" },
  { id: 2, name: "Alpha", volume: 2, market: "futures" },
  { id: 3, name: "Gamma", volume: 2, market: "spot" },
];
const columns: DataColumn<typeof rows[number]>[] = [
  { key: "name", label: "Name", value: (row) => row.name },
  { key: "volume", label: "Volume", value: (row) => row.volume },
  { key: "market", label: "Market", value: (row) => row.market, filter: true },
];

function renderTable(exportCsv = vi.fn()) {
  return render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id}
    label="instruments" storageId="instruments" exportCsv={exportCsv} />);
}

function names() {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent);
}

afterEach(() => {
  cleanup();
  localStorage.removeItem("signal-bot:table-columns:instruments");
});

it("cycles numeric and text sorting and preserves the order of tied rows", () => {
  renderTable();
  const volume = screen.getByRole("button", { name: "Volume" });
  fireEvent.click(volume);
  expect(names()).toEqual(["Alpha", "Gamma", "Beta"]);
  expect(screen.getByRole("columnheader", { name: "Volume" })).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(volume);
  expect(names()).toEqual(["Beta", "Alpha", "Gamma"]);
  expect(screen.getByRole("columnheader", { name: "Volume" })).toHaveAttribute("aria-sort", "descending");
  fireEvent.click(volume);
  expect(screen.getByRole("columnheader", { name: "Volume" })).toHaveAttribute("aria-sort", "none");
  fireEvent.click(screen.getByRole("button", { name: "Name" }));
  expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
});

it("filters, searches, and exports the displayed rows in order", () => {
  const exportCsv = vi.fn();
  renderTable(exportCsv);
  fireEvent.change(screen.getByRole("combobox", { name: "Filter by Market" }), { target: { value: "spot" } });
  fireEvent.click(screen.getByRole("button", { name: "Volume" }));
  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  expect(exportCsv).toHaveBeenCalledWith([rows[2], rows[0]], columns);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search instruments" }), { target: { value: "beta" } });
  expect(names()).toEqual(["Beta"]);
});

it("persists column visibility between mounts", () => {
  const { unmount } = renderTable();
  fireEvent.click(screen.getByText("Columns"));
  fireEvent.click(screen.getByRole("checkbox", { name: "Show Volume" }));
  expect(screen.queryByRole("columnheader", { name: "Volume" })).not.toBeInTheDocument();
  unmount();
  renderTable();
  expect(screen.queryByRole("columnheader", { name: "Volume" })).not.toBeInTheDocument();
});
