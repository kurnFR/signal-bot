import { useMemo, useState } from "react";
import { Download, Filter, Search } from "lucide-react";
import type { PaperOrder } from "../lib/paperTrading";

interface ExecutionBlotterProps {
  orders: PaperOrder[];
  canTrade?: boolean;
  onCancelOrder?: (order: PaperOrder) => void;
  cancellingOrderId?: number | null;
}

type SortField =
  | "id"
  | "symbol"
  | "direction"
  | "status"
  | "requested_entry_price"
  | "fill_price"
  | "created_at"
  | "state_reason";

export function ExecutionBlotter({
  orders,
  canTrade = false,
  onCancelOrder,
  cancellingOrderId,
}: ExecutionBlotterProps) {
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [globalFilter, setGlobalFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [pageIndex, setPageIndex] = useState(0);
  const pageSize = 15;

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDirection("desc");
    }
    setPageIndex(0);
  };

  const filteredOrders = useMemo(() => {
    let result = orders;
    if (statusFilter !== "ALL") {
      result = result.filter((o) => o.status === statusFilter);
    }
    const q = globalFilter.trim().toLowerCase();
    if (q) {
      result = result.filter(
        (o) =>
          o.symbol.toLowerCase().includes(q) ||
          String(o.id).includes(q) ||
          o.direction.toLowerCase().includes(q) ||
          (o.state_reason && o.state_reason.toLowerCase().includes(q))
      );
    }

    return [...result].sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "id":
          cmp = a.id - b.id;
          break;
        case "symbol":
          cmp = a.symbol.localeCompare(b.symbol);
          break;
        case "direction":
          cmp = a.direction.localeCompare(b.direction);
          break;
        case "status":
          cmp = a.status.localeCompare(b.status);
          break;
        case "requested_entry_price":
          cmp = (a.requested_entry_price ?? 0) - (b.requested_entry_price ?? 0);
          break;
        case "fill_price":
          cmp = (a.fill_price ?? 0) - (b.fill_price ?? 0);
          break;
        case "created_at":
          cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "state_reason":
          cmp = (a.state_reason || "").localeCompare(b.state_reason || "");
          break;
      }
      return sortDirection === "asc" ? cmp : -cmp;
    });
  }, [orders, statusFilter, globalFilter, sortField, sortDirection]);

  const totalPages = Math.ceil(filteredOrders.length / pageSize) || 1;
  const paginatedOrders = useMemo(() => {
    const start = pageIndex * pageSize;
    return filteredOrders.slice(start, start + pageSize);
  }, [filteredOrders, pageIndex, pageSize]);

  const exportCsv = () => {
    if (!orders.length) return;
    const headers = [
      "ID",
      "Symbol",
      "Market",
      "Timeframe",
      "Direction",
      "Status",
      "Requested",
      "FillPrice",
      "Created",
      "Reason",
    ];
    const rows = orders.map((o) => [
      o.id,
      o.symbol,
      o.market,
      o.timeframe,
      o.direction,
      o.status,
      o.requested_entry_price ?? "",
      o.fill_price ?? "",
      o.created_at,
      `"${(o.state_reason || "").replace(/"/g, '""')}"`,
    ]);
    const csvContent = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `order_blotter_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const renderSortIndicator = (field: SortField) => {
    if (sortField !== field) return null;
    return sortDirection === "asc" ? " 🔼" : " 🔽";
  };

  return (
    <div className="execution-blotter-wrapper">
      <div
        className="blotter-controls"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "10px",
          marginBottom: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", position: "relative" }}>
            <Search size={14} style={{ position: "absolute", left: "10px", color: "var(--color-muted)" }} />
            <input
              type="text"
              placeholder="Search symbol, reason…"
              value={globalFilter}
              onChange={(e) => {
                setGlobalFilter(e.target.value);
                setPageIndex(0);
              }}
              className="text-input"
              style={{ paddingLeft: "30px", minWidth: "200px", height: "32px", fontSize: "12px" }}
            />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <Filter size={14} style={{ color: "var(--color-muted)" }} />
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPageIndex(0);
              }}
              className="select-input"
              style={{ height: "32px", fontSize: "12px", padding: "0 8px" }}
            >
              <option value="ALL">All Statuses</option>
              <option value="PENDING">Pending</option>
              <option value="FILLED">Filled</option>
              <option value="CANCELLED">Cancelled</option>
              <option value="REJECTED">Rejected</option>
            </select>
          </div>
        </div>

        <button
          type="button"
          onClick={exportCsv}
          className="button button-secondary button-sm"
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
        >
          <Download size={14} /> Export CSV
        </button>
      </div>

      <div className="status-table-wrap">
        <table className="status-table">
          <thead>
            <tr>
              <th onClick={() => handleSort("id")} style={{ cursor: "pointer", width: "70px" }}>
                #{renderSortIndicator("id")}
              </th>
              <th onClick={() => handleSort("symbol")} style={{ cursor: "pointer" }}>
                Symbol{renderSortIndicator("symbol")}
              </th>
              <th onClick={() => handleSort("direction")} style={{ cursor: "pointer" }}>
                Direction{renderSortIndicator("direction")}
              </th>
              <th onClick={() => handleSort("status")} style={{ cursor: "pointer" }}>
                Status{renderSortIndicator("status")}
              </th>
              <th onClick={() => handleSort("requested_entry_price")} style={{ cursor: "pointer" }}>
                Requested{renderSortIndicator("requested_entry_price")}
              </th>
              <th onClick={() => handleSort("fill_price")} style={{ cursor: "pointer" }}>
                Fill Price{renderSortIndicator("fill_price")}
              </th>
              <th onClick={() => handleSort("created_at")} style={{ cursor: "pointer" }}>
                Created{renderSortIndicator("created_at")}
              </th>
              <th onClick={() => handleSort("state_reason")} style={{ cursor: "pointer" }}>
                Reason{renderSortIndicator("state_reason")}
              </th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {paginatedOrders.length ? (
              paginatedOrders.map((order) => {
                const pillClass =
                  order.status === "FILLED"
                    ? "status-positive"
                    : order.status === "PENDING"
                    ? "status-warning"
                    : order.status === "CANCELLED"
                    ? "status-neutral"
                    : "status-negative";

                const dateObj = new Date(order.created_at);
                const timeStr = isNaN(dateObj.getTime())
                  ? order.created_at
                  : dateObj.toLocaleTimeString();

                return (
                  <tr key={order.id}>
                    <td>#{order.id}</td>
                    <td>
                      <div>
                        <strong>{order.symbol}</strong>
                        <div style={{ fontSize: "11px", color: "var(--color-muted)" }}>
                          {order.market} · {order.timeframe}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span
                        style={{
                          color: order.direction === "LONG" ? "#10b981" : "#ef4444",
                          fontWeight: 600,
                        }}
                      >
                        {order.direction}
                      </span>
                    </td>
                    <td>
                      <span className={`status-pill ${pillClass}`}>{order.status}</span>
                    </td>
                    <td>
                      {order.requested_entry_price != null
                        ? `$${order.requested_entry_price.toLocaleString()}`
                        : "Unset"}
                    </td>
                    <td>
                      {order.fill_price != null ? `$${order.fill_price.toLocaleString()}` : "—"}
                    </td>
                    <td>{timeStr}</td>
                    <td>{order.state_reason || "—"}</td>
                    <td>
                      {canTrade && order.status === "PENDING" ? (
                        <button
                          type="button"
                          className="button button-danger-subtle button-xs"
                          aria-label={`Cancel pending order #${order.id} for ${order.symbol}`}
                          onClick={() => onCancelOrder?.(order)}
                          disabled={cancellingOrderId === order.id}
                        >
                          {cancellingOrderId === order.id ? "Cancelling…" : "Cancel"}
                        </button>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={9} style={{ textAlign: "center", padding: "24px" }}>
                  No execution orders found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div
          className="blotter-pagination"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: "12px",
            fontSize: "12px",
          }}
        >
          <div>
            Page {pageIndex + 1} of {totalPages} ({filteredOrders.length} total)
          </div>
          <div style={{ display: "flex", gap: "6px" }}>
            <button
              type="button"
              className="button button-secondary button-xs"
              onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
              disabled={pageIndex === 0}
            >
              Previous
            </button>
            <button
              type="button"
              className="button button-secondary button-xs"
              onClick={() => setPageIndex((p) => Math.min(totalPages - 1, p + 1))}
              disabled={pageIndex >= totalPages - 1}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
