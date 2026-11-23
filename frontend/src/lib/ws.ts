import { useProTraderStore } from "./store";

export type WsChannel = "market" | "paper" | "system";

export interface WsClientOptions {
  onMessage?: (data: any) => void;
  onStatusChange?: (status: "connecting" | "connected" | "disconnected") => void;
  subscribeSymbol?: string;
  subscribeMarket?: string;
  subscribeTimeframe?: string;
}

export function connectWebSocketChannel(
  channel: WsChannel,
  options: WsClientOptions = {},
): () => void {
  let ws: WebSocket | null = null;
  let closedManually = false;
  let retryCount = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;

  const setStatus = (status: "connecting" | "connected" | "disconnected") => {
    try {
      useProTraderStore.getState().setWsStatus(channel, status);
    } catch {
      // Ignore in non-browser or testing contexts
    }
    options.onStatusChange?.(status);
  };

  const getWebSocketUrl = (): string | null => {
    if (typeof window === "undefined") return null;
    let token: string | null = null;
    try {
      token = window.localStorage.getItem("token");
    } catch {
      // Storage unavailable
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = new URL(`${protocol}//${host}/ws/${channel}`);
    if (token) {
      url.searchParams.set("token", token.trim());
    }
    return url.toString();
  };

  const startPing = (socket: WebSocket) => {
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: "ping" }));
      }
    }, 25000);
  };

  const stopPing = () => {
    if (pingTimer) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  };

  const connect = () => {
    if (closedManually) return;
    const url = getWebSocketUrl();
    if (!url) return;

    setStatus("connecting");
    try {
      ws = new WebSocket(url);
    } catch {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      retryCount = 0;
      setStatus("connected");
      if (ws) startPing(ws);

      // If market channel and custom subscription requested, send subscribe frame
      if (channel === "market" && options.subscribeSymbol && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          action: "subscribe",
          symbol: options.subscribeSymbol,
          market: options.subscribeMarket ?? "spot",
          timeframe: options.subscribeTimeframe ?? "1h",
        }));
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        options.onMessage?.(data);

        // Store integration
        const store = useProTraderStore.getState();
        if (data.type === "market_snapshot" && data.symbol) {
          store.setMarketPrice(data.symbol, {
            price: data.mark_price ?? (data.candles?.length ? data.candles[data.candles.length - 1].close : 0),
            markPrice: data.mark_price,
            fundingRate: data.funding_rate,
          });
        } else if (data.type === "paper_snapshot") {
          store.setPaperData(data.positions ?? [], data.breaker ?? null);
        } else if (data.type === "system_snapshot") {
          store.setSystemStatus(data.status ?? {});
        }
      } catch {
        // Non-JSON frame ignored
      }
    };

    ws.onerror = () => {
      // Socket error will trigger onclose
    };

    ws.onclose = () => {
      stopPing();
      setStatus("disconnected");
      ws = null;
      if (!closedManually) {
        scheduleReconnect();
      }
    };
  };

  const scheduleReconnect = () => {
    if (retryTimer) clearTimeout(retryTimer);
    const delay = Math.min(1000 * Math.pow(1.5, retryCount), 10000);
    retryCount++;
    retryTimer = setTimeout(() => {
      connect();
    }, delay);
  };

  connect();

  return () => {
    closedManually = true;
    stopPing();
    if (retryTimer) clearTimeout(retryTimer);
    if (ws) {
      try {
        ws.close();
      } catch {
        // Ignore close errors on unmount
      }
      ws = null;
    }
    setStatus("disconnected");
  };
}
