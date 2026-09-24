import { afterEach, describe, expect, it, vi } from "vitest";

// Capture mithril's request so the test drives the backend's answers without a network call;
// redraw is recorded and apiUrl is identity so URLs are predictable.
const { mockRequest, mockRedraw } = vi.hoisted(() => ({ mockRequest: vi.fn(), mockRedraw: vi.fn() }));
vi.mock("mithril", () => ({ default: { request: mockRequest, redraw: mockRedraw } }));
vi.mock("@imbue/workspace-ui/src/base-path", () => ({ apiUrl: (path: string) => path }));

const OFF = { mode: "off" as const, tier: null, exhausted_accounts: [] };
const ON = { mode: "auto" as const, tier: "complex" as const, exhausted_accounts: ["spent"] };

async function freshModule(): Promise<typeof import("./Routing")> {
  vi.resetModules();
  mockRequest.mockReset();
  mockRedraw.mockClear();
  return import("./Routing");
}

describe("ensureRoutingState", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads a chat's state once, shares the load, redraws when it lands, and keeps chats apart", async () => {
    const routing = await freshModule();
    mockRequest.mockResolvedValueOnce({ state: ON });

    const [first, second] = await Promise.all([
      routing.ensureRoutingState("agent-1"),
      routing.ensureRoutingState("agent-1"),
    ]);

    expect(first).toEqual(ON);
    expect(second).toEqual(ON);
    expect(mockRequest).toHaveBeenCalledTimes(1);
    expect(mockRequest).toHaveBeenLastCalledWith(
      expect.objectContaining({ method: "GET", url: "/api/chats/:chatId/routing", params: { chatId: "agent-1" } }),
    );
    expect(mockRedraw).toHaveBeenCalledTimes(1);
    expect(routing.getRoutingState("agent-1")).toEqual(ON);
    expect(routing.getRoutingState("agent-2")).toBeNull();
  });

  it("answers null on a failed load and holds off asking again for a while", async () => {
    vi.useFakeTimers();
    const routing = await freshModule();
    mockRequest.mockRejectedValueOnce(new Error("503"));

    expect(await routing.ensureRoutingState("agent-1")).toBeNull();
    expect(await routing.ensureRoutingState("agent-1")).toBeNull();
    expect(mockRequest).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(routing.RETRY_DELAY_MS);
    mockRequest.mockResolvedValueOnce({ state: ON });
    expect(await routing.ensureRoutingState("agent-1")).toEqual(ON);
    expect(mockRequest).toHaveBeenCalledTimes(2);
  });
});

describe("updateRoutingState", () => {
  it("shows the new state at once, keeps what the backend answers, and puts the old one back on a refusal", async () => {
    const routing = await freshModule();
    mockRequest.mockResolvedValueOnce({ state: OFF });
    await routing.ensureRoutingState("agent-1");

    // The backend clears what the chat had given up on when routing is turned back on, and its
    // answer is what the page keeps -- not the body that was sent.
    const answered = { mode: "auto" as const, tier: null, exhausted_accounts: [] };
    mockRequest.mockResolvedValueOnce({ state: answered });
    const pending = routing.updateRoutingState("agent-1", { ...answered, exhausted_accounts: ["spent"] });
    expect(routing.getRoutingState("agent-1")).toEqual({ ...answered, exhausted_accounts: ["spent"] });
    expect(await pending).toEqual(answered);
    expect(routing.getRoutingState("agent-1")).toEqual(answered);
    expect(mockRequest).toHaveBeenLastCalledWith(
      expect.objectContaining({ method: "PUT", url: "/api/chats/:chatId/routing" }),
    );

    mockRequest.mockRejectedValueOnce(new Error("500"));
    expect(await routing.updateRoutingState("agent-1", OFF)).toEqual(answered);
    expect(routing.getRoutingState("agent-1")).toEqual(answered);
  });
});

describe("toggledRoutingState", () => {
  it("flips only the mode, so what the chat has learned survives being turned off and on", async () => {
    const routing = await freshModule();
    expect(routing.toggledRoutingState(ON)).toEqual({ ...ON, mode: "off" });
    expect(routing.toggledRoutingState(OFF)).toEqual({ ...OFF, mode: "auto" });
    expect(routing.toggledRoutingState(routing.toggledRoutingState(ON))).toEqual(ON);
  });
});

describe("routingLabel", () => {
  it("says whether the chat is picking its own model", async () => {
    const routing = await freshModule();
    expect(routing.routingLabel(ON)).toBe("On");
    expect(routing.routingLabel(OFF)).toBe("Off");
  });
});
