import "@testing-library/jest-dom/vitest";

// Ensure deterministic locale formatting in test environment across all operating systems
const OriginalNumberFormat = Intl.NumberFormat;
// @ts-ignore
Intl.NumberFormat = function (locales?: string | string[], options?: Intl.NumberFormatOptions) {
  return new OriginalNumberFormat(locales || "en-US", options);
} as any;
Object.defineProperty(Intl.NumberFormat, "prototype", {
  value: OriginalNumberFormat.prototype,
  writable: true,
});
(Intl.NumberFormat as any).supportedLocalesOf = OriginalNumberFormat.supportedLocalesOf;

const originalNumberToLocaleString = Number.prototype.toLocaleString;
Number.prototype.toLocaleString = function (locales?: string | string[], options?: Intl.NumberFormatOptions) {
  return originalNumberToLocaleString.call(this, locales || "en-US", options);
};

// Polyfill HTMLDialogElement showModal and close for JSDOM
if (typeof HTMLDialogElement !== "undefined") {
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement, returnValue?: string) {
    this.open = false;
    this.removeAttribute("open");
    if (returnValue !== undefined) {
      this.returnValue = returnValue;
    }
    this.dispatchEvent(new Event("close"));
  };
}

// Polyfill HTMLCanvasElement.prototype.getContext for JSDOM
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, contextType: string) {
    if (contextType === "2d") {
      const baseCtx: any = {
        canvas: this,
        measureText: () => ({ width: 10, height: 10 }),
        getImageData: () => ({ data: new Uint8ClampedArray(4) }),
        createImageData: () => ({ data: new Uint8ClampedArray(4) }),
        createLinearGradient: () => ({ addColorStop: () => {} }),
        createRadialGradient: () => ({ addColorStop: () => {} }),
      };
      return new Proxy(baseCtx, {
        get(target, prop) {
          if (prop in target) return target[prop];
          return () => {};
        },
        set(target, prop, value) {
          target[prop] = value;
          return true;
        },
      });
    }
    return null;
  } as any;
}

// Polyfill window.matchMedia and ResizeObserver for JSDOM (required by lightweight-charts)
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as any);
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
}
