import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

Object.defineProperty(Element.prototype, "scrollIntoView", {
  configurable: true,
  value: vi.fn(),
});

if (globalThis.crypto.randomUUID === undefined) {
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    configurable: true,
    value: () => "00000000-0000-4000-8000-000000000099",
  });
}
