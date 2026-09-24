/**
 * Whether a chat picks its own model (the backend's ``ChatRoutingState``, at
 * ``/api/chats/<id>/routing``). When it is on, the chat weighs each of your turns before it runs
 * and moves itself to a model that fits -- to a quicker one for small things, to a stronger one
 * for hard things, and to another provider when its own cannot serve the work or has stopped
 * answering.
 *
 * The choice belongs to the chat, so it lives on the backend beside the chat's record and this
 * page keeps one copy per chat, loaded on demand and replaced whole by every write. The shape of
 * this module deliberately mirrors ``FastMode.ts``: they are the same kind of per-chat setting and
 * a reader who knows one should recognize the other.
 */

import m from "mithril";
import { apiUrl } from "@imbue/workspace-ui/src/base-path";

export type RoutingMode = "off" | "auto";

export interface ChatRoutingState {
  mode: RoutingMode;
  // The difficulty the chat last settled on, so a bare "keep going" inherits it. Display only.
  tier: "routine" | "standard" | "complex" | null;
  // The accounts that stopped answering this chat, which it will not move back to.
  exhausted_accounts: string[];
}

const stateByChat = new Map<string, ChatRoutingState>();
const loadingByChat = new Map<string, Promise<ChatRoutingState | null>>();
// After a failed load, when the backend may be asked again for that chat; the callers ask on
// every render, so a failure has to hold them off rather than be retried by the next redraw.
const retryNotBeforeByChat = new Map<string, number>();
export const RETRY_DELAY_MS = 30_000;

/** The chat's routing state as this page last saw it, or null before the first load answered. */
export function getRoutingState(chatId: string): ChatRoutingState | null {
  return stateByChat.get(chatId) ?? null;
}

/**
 * Load the chat's routing state once and redraw when it lands; later calls share the first load. A
 * load that fails is warned about and resolves null, leaving nothing loaded until
 * ``RETRY_DELAY_MS`` has passed.
 */
export function ensureRoutingState(chatId: string): Promise<ChatRoutingState | null> {
  const known = stateByChat.get(chatId);
  if (known !== undefined) return Promise.resolve(known);
  const loading = loadingByChat.get(chatId);
  if (loading !== undefined) return loading;
  if (Date.now() < (retryNotBeforeByChat.get(chatId) ?? 0)) return Promise.resolve(null);
  const request = m
    .request<{ state: ChatRoutingState }>({
      method: "GET",
      url: apiUrl("/api/chats/:chatId/routing"),
      params: { chatId },
    })
    .then((response) => {
      stateByChat.set(chatId, response.state);
      m.redraw();
      return response.state;
    })
    .catch((error: unknown) => {
      console.warn(`Failed to load the routing state of chat ${chatId}`, error);
      retryNotBeforeByChat.set(chatId, Date.now() + RETRY_DELAY_MS);
      return null;
    })
    .finally(() => {
      loadingByChat.delete(chatId);
    });
  loadingByChat.set(chatId, request);
  return request;
}

/**
 * Replace the chat's routing state, on the page at once and on the backend; the backend's answer
 * is what stays. A write the backend refuses or never receives puts the previous state back.
 */
export async function updateRoutingState(chatId: string, next: ChatRoutingState): Promise<ChatRoutingState | null> {
  const previous = stateByChat.get(chatId);
  stateByChat.set(chatId, next);
  m.redraw();
  try {
    const response = await m.request<{ state: ChatRoutingState }>({
      method: "PUT",
      url: apiUrl("/api/chats/:chatId/routing"),
      params: { chatId },
      body: next,
    });
    stateByChat.set(chatId, response.state);
    return response.state;
  } catch (error) {
    console.warn(`Failed to save the routing state of chat ${chatId}`, error);
    if (previous === undefined) stateByChat.delete(chatId);
    else stateByChat.set(chatId, previous);
    return previous ?? null;
  } finally {
    m.redraw();
  }
}

/** The state a click on the routing row should write: the other mode, keeping what the chat learned. */
export function toggledRoutingState(state: ChatRoutingState): ChatRoutingState {
  return { ...state, mode: state.mode === "auto" ? "off" : "auto" };
}

/** What the model picker's routing row says for a state. */
export function routingLabel(state: ChatRoutingState): string {
  return state.mode === "auto" ? "On" : "Off";
}

/** Forget every chat's state, so a test starts clean. */
export function resetRoutingForTests(): void {
  stateByChat.clear();
  loadingByChat.clear();
  retryNotBeforeByChat.clear();
}
