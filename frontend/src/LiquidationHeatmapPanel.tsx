import { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ApiError, getJson } from "./lib/api";

type Cell = [column: number, row: number, value: number];
interface Heatmap {
  levels: number[];
  candles: [time: number, close: number | null][];
  cells: Cell[];
  updatedAt: number;
  price: number;
  priceStale: boolean;
}

const WINDOWS = ["12h", "24h", "3d", "7d"] as const;
const priceFormat = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 7 });
const notionalFormat = new Intl.NumberFormat(undefined, {
  style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2,
});
const chartTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});
const BACKGROUND = "#101625";

function HeatmapChart({ data }: { data: Heatmap }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [cursor, setCursor] = useState<{ column: number; row: number } | null>(null);
  const columns = data.candles.length;
  const rows = data.levels.length;
  const cells = useMemo(() => new Map(data.cells.map(([column, row, value]) => [column * rows + row, value])), [data.cells, rows]);
  const [low, high] = [Math.log(data.levels[0]), Math.log(data.levels[rows - 1])];
  const rowPosition = (price: number) => (Math.log(price) - low) / (high - low) * (rows - 1) + 0.5;

  useEffect(() => {
    const context = canvas.current?.getContext("2d");
    if (!context) return;
    const cellWidth = context.canvas.width / columns;
    const cellHeight = context.canvas.height / rows;
    const peak = data.cells.reduce((max, [, , amount]) => Math.max(max, amount), 0);
    context.fillStyle = BACKGROUND;
    context.fillRect(0, 0, context.canvas.width, context.canvas.height);
    for (const [column, row, value] of data.cells) {
      const strength = Math.sqrt(value / peak);
      context.fillStyle = `hsl(${225 - 174 * strength} 78% ${15 + 51 * strength}%)`;
      context.fillRect(column * cellWidth, (rows - 1 - row) * cellHeight, Math.ceil(cellWidth), Math.ceil(cellHeight));
    }
    context.beginPath();
    let connected = false;
    data.candles.forEach(([, close], column) => {
      if (close === null) {
        connected = false;
        return;
      }
      const position = (Math.log(close) - low) / (high - low) * (rows - 1) + 0.5;
      const x = (column + 0.5) * cellWidth;
      const y = (rows - position) * cellHeight;
      if (connected) context.lineTo(x, y);
      else context.moveTo(x, y);
      connected = true;
    });
    context.strokeStyle = "#0b0f19";
    context.lineWidth = 4;
    context.stroke();
    context.strokeStyle = "#fff";
    context.lineWidth = 1.5;
    context.stroke();
  }, [data, columns, rows, low, high]);

  const selected = cursor ?? { column: columns - 1, row: Math.max(0, Math.min(rows - 1, Math.floor(rowPosition(data.price)))) };
  const price = data.levels[selected.row];
  const timestamp = data.candles[selected.column][0];
  const amount = cells.get(selected.column * rows + selected.row) ?? 0;

  return <figure className="liquidation-figure" aria-label="Estimated liquidation heatmap by time and price">
    <div className="liquidation-plot">
      <div className="liquidation-grid" tabIndex={0} role="group"
        aria-label="Inspect chart with the arrow keys"
        onKeyDown={(event) => {
          const offset = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[event.key];
          if (!offset) return;
          event.preventDefault();
          setCursor({
            column: Math.max(0, Math.min(columns - 1, selected.column + offset[0])),
            row: Math.max(0, Math.min(rows - 1, selected.row + offset[1])),
          });
        }}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const column = Math.floor((event.clientX - rect.left) / rect.width * columns);
          const row = rows - 1 - Math.floor((event.clientY - rect.top) / rect.height * rows);
          if (column >= 0 && column < columns && row >= 0 && row < rows) setCursor({ column, row });
        }}>
        <canvas ref={canvas} width={1200} height={460} aria-hidden="true" />
        {cursor && <div className="liquidation-crosshair" aria-hidden="true" style={{
          left: `${(selected.column + 0.5) / columns * 100}%`,
          top: `${(rows - selected.row - 0.5) / rows * 100}%`,
        }} />}
      </div>
      <div className="liquidation-prices" aria-hidden="true">
        {[1, 0.75, 0.5, 0.25, 0].map((fraction) => <span key={fraction}>{priceFormat.format(data.levels[Math.round((rows - 1) * fraction)])}</span>)}
      </div>
      <div className="liquidation-times">
        <span>{chartTime.format(data.candles[0][0])}</span>
        <span>{chartTime.format(data.candles[columns - 1][0])} WIB</span>
      </div>
    </div>
    <figcaption className="liquidation-caption">
      <div><span className="liquidation-scale" aria-hidden="true" /> Low to high estimated concentration <span className="liquidation-close-mark" aria-hidden="true" /> Close price</div>
      <output>{chartTime.format(timestamp)} WIB · ${priceFormat.format(price)} · {notionalFormat.format(amount)} estimated</output>
    </figcaption>
  </figure>;
}

export function LiquidationHeatmapPanel() {
  const [symbols, setSymbols] = useState(["BTC"]);
  const [symbol, setSymbol] = useState("BTC");
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("24h");
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<Heatmap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getJson<{ symbols: string[] }>("/api/liquidations/markets", controller.signal)
      .then((result) => { if (!controller.signal.aborted && result.symbols.length) setSymbols(result.symbols); })
      .catch(() => { /* BTC remains available when the market list cannot load. */ });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    const query = new URLSearchParams({ symbol, window });
    void getJson<{ heatmap: Heatmap | null }>(`/api/liquidations/heatmap?${query}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setData(result.heatmap); })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(cause instanceof ApiError && cause.detail ? cause.detail : "Could not load the liquidation heatmap.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [symbol, window, attempt]);

  const peaks = useMemo(() => {
    let lower: Cell | undefined;
    let upper: Cell | undefined;
    if (!data) return { lower, upper };
    for (const cell of data.cells) {
      const [column, row, notional] = cell;
      if (column !== data.candles.length - 1) continue;
      if (data.levels[row] < data.price && notional > (lower?.[2] ?? 0)) lower = cell;
      if (data.levels[row] > data.price && notional > (upper?.[2] ?? 0)) upper = cell;
    }
    return { lower, upper };
  }, [data]);

  return <section className="panel liquidation-panel" aria-labelledby="liquidation-chart-title" aria-busy={loading}>
    <div className="panel-heading liquidation-heading">
      <div>
        <h2 id="liquidation-chart-title">Potential liquidation zones</h2>
        <p>Bright bands show larger modeled liquidation clusters. Shorts are above price and longs are below.</p>
      </div>
      <div className="liquidation-controls">
        <label className="screener-control-field"><span id="liquidation-asset-label">Asset</span><select aria-labelledby="liquidation-asset-label" value={symbol} onChange={(event) => setSymbol(event.target.value)}>
          {symbols.map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <label className="screener-control-field"><span id="liquidation-window-label">Lookback</span><select aria-labelledby="liquidation-window-label" value={window} onChange={(event) => setWindow(event.target.value as typeof window)}>
          {WINDOWS.map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <button type="button" className="button button-secondary button-sm" onClick={() => setAttempt((value) => value + 1)} disabled={loading}>
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </button>
      </div>
    </div>
    {loading ? <p className="empty-state" role="status">Loading {symbol} liquidation zones…</p>
      : error ? <p className="control-feedback control-error" role="alert">{error}</p>
      : !data?.cells.length ? <p className="empty-state">No modeled clusters are available for {symbol} in this window.</p>
      : <>
        <dl className="liquidation-summary">
          <div><dt>Strongest long zone</dt><dd>{peaks.lower ? `$${priceFormat.format(data.levels[peaks.lower[1]])}` : "No zone"}</dd>
            {peaks.lower && <dd>{notionalFormat.format(peaks.lower[2])} estimated</dd>}</div>
          <div><dt>Reference price</dt><dd>${priceFormat.format(data.price)}</dd><dd>{data.priceStale ? "Delayed price" : `${chartTime.format(data.updatedAt)} WIB`}</dd></div>
          <div><dt>Strongest short zone</dt><dd>{peaks.upper ? `$${priceFormat.format(data.levels[peaks.upper[1]])}` : "No zone"}</dd>
            {peaks.upper && <dd>{notionalFormat.format(peaks.upper[2])} estimated</dd>}</div>
        </dl>
        <HeatmapChart key={`${symbol}-${window}-${data.updatedAt}`} data={data} />
      </>}
    <p className="page-description">Multi-exchange model estimates based on changes in open interest. <a href="https://swolecharts.com/liquidation-heatmap" target="_blank" rel="noreferrer">Data and methodology by Swolecharts</a>.</p>
  </section>;
}
