import { create } from "zustand";

export interface MarketPriceInfo {
  price: number;
  markPrice?: number | null;
  fundingRate?: number | null;
  lastUpdated: number;
}

export interface ProTraderState {
  // Navigation & Market selections
  selectedSymbol: string;
  selectedTimeframe: string;
  selectedMarket: "spot" | "futures";
  setSelectedSymbol: (symbol: string) => void;
  setSelectedTimeframe: (timeframe: string) => void;
  setSelectedMarket: (market: "spot" | "futures") => void;

  // Real-time market prices & funding
  marketPrices: Record<string, MarketPriceInfo>;
  setMarketPrice: (symbol: string, info: Partial<MarketPriceInfo>) => void;

  // Live Paper positions & circuit breakers
  paperPositions: Array<any>;
  circuitBreaker: Record<string, any> | null;
  setPaperData: (positions: Array<any>, breaker?: Record<string, any> | null) => void;

  // Real-time system health
  systemStatus: Record<string, any> | null;
  setSystemStatus: (status: Record<string, any>) => void;

  // WebSocket status
  wsStatus: {
    market: "disconnected" | "connecting" | "connected";
    paper: "disconnected" | "connecting" | "connected";
    system: "disconnected" | "connecting" | "connected";
  };
  setWsStatus: (channel: "market" | "paper" | "system", status: "disconnected" | "connecting" | "connected") => void;
}

export const useProTraderStore = create<ProTraderState>((set) => ({
  selectedSymbol: "BTCUSDT",
  selectedTimeframe: "1h",
  selectedMarket: "spot",
  setSelectedSymbol: (selectedSymbol) => set({ selectedSymbol: selectedSymbol.toUpperCase() }),
  setSelectedTimeframe: (selectedTimeframe) => set({ selectedTimeframe }),
  setSelectedMarket: (selectedMarket) => set({ selectedMarket }),

  marketPrices: {},
  setMarketPrice: (symbol, info) =>
    set((state) => {
      const sym = symbol.toUpperCase();
      const existing = state.marketPrices[sym] ?? { price: 0, lastUpdated: Date.now() };
      return {
        marketPrices: {
          ...state.marketPrices,
          [sym]: {
            ...existing,
            ...info,
            lastUpdated: Date.now(),
          },
        },
      };
    }),

  paperPositions: [],
  circuitBreaker: null,
  setPaperData: (paperPositions, circuitBreaker = null) =>
    set({ paperPositions, ...(circuitBreaker ? { circuitBreaker } : {}) }),

  systemStatus: null,
  setSystemStatus: (systemStatus) => set({ systemStatus }),

  wsStatus: {
    market: "disconnected",
    paper: "disconnected",
    system: "disconnected",
  },
  setWsStatus: (channel, status) =>
    set((state) => ({
      wsStatus: {
        ...state.wsStatus,
        [channel]: status,
      },
    })),
}));
