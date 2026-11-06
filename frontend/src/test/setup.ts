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
