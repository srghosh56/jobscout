// useChatStream: a custom React hook that POSTs a question to /api/chat and reads the
// Server-Sent Events stream with fetch + ReadableStream (EventSource can't send POST
// bodies or headers). It exposes the growing answer, tool events and the final result.
import { useCallback, useRef, useState } from "react";
import { getApiKey } from "../api/client";
import { SSEParser } from "../lib/sse";

export interface ToolEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  seconds?: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  tools: ToolEvent[];
  citations: string[];
  warnings: string[];
  traceId?: string;
  messageId?: string;
  question?: string;
  pending?: boolean;
  error?: string;
}

export interface ChatDone {
  answer: string;
  citations: string[];
  warnings: string[];
  trace_id?: string;
  message_id?: string;
  session_id?: string;
}

type Fetcher = typeof fetch;

export function useChatStream(fetcher: Fetcher = (...a) => fetch(...a)) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  // useRef keeps a value across renders without causing a re-render when it changes.
  const sessionId = useRef<string>(crypto.randomUUID().replace(/-/g, ""));
  const abortRef = useRef<AbortController | null>(null);

  const updateLast = (fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1]!)]);

  const send = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q) return;
      setMessages((prev) => [
        ...prev,
        { role: "user", text: q, tools: [], citations: [], warnings: [] },
        {
          role: "assistant",
          text: "",
          tools: [],
          citations: [],
          warnings: [],
          pending: true,
          question: q,
        },
      ]);
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const res = await fetcher("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-API-Key": await getApiKey() },
          body: JSON.stringify({ message: q, session_id: sessionId.current }),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          const msg =
            res.status === 429
              ? "Too many requests, please wait a moment."
              : `Request failed (${res.status})`;
          throw new Error(msg);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SSEParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          for (const ev of parser.feed(decoder.decode(value, { stream: true }))) {
            handleEvent(ev.event, JSON.parse(ev.data) as Record<string, unknown>);
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          updateLast((m) => ({ ...m, pending: false, error: (err as Error).message }));
        }
      } finally {
        setStreaming(false);
        updateLast((m) => ({ ...m, pending: false }));
      }

      function handleEvent(type: string, data: Record<string, unknown>) {
        switch (type) {
          case "token":
            updateLast((m) => ({ ...m, text: m.text + String(data.text ?? "") }));
            break;
          case "reset": // the server is regenerating the answer (citation check failed)
            updateLast((m) => ({ ...m, text: "" }));
            break;
          case "tool_call":
            updateLast((m) => ({
              ...m,
              tools: [
                ...m.tools,
                {
                  id: String(data.id),
                  name: String(data.name),
                  args: (data.args ?? {}) as Record<string, unknown>,
                },
              ],
            }));
            break;
          case "tool_result":
            updateLast((m) => ({
              ...m,
              tools: m.tools.map((t) =>
                t.id === data.id
                  ? { ...t, result: data.result, seconds: Number(data.seconds ?? 0) }
                  : t,
              ),
            }));
            break;
          case "citation":
            updateLast((m) => ({ ...m, citations: (data.ids as string[]) ?? [] }));
            break;
          case "done": {
            const d = data as unknown as ChatDone;
            updateLast((m) => ({
              ...m,
              text: d.answer,
              citations: d.citations ?? [],
              warnings: d.warnings ?? [],
              traceId: d.trace_id,
              messageId: d.message_id,
              pending: false,
            }));
            break;
          }
          case "error":
            updateLast((m) => ({ ...m, error: String(data.message ?? "error") }));
            break;
        }
      }
    },
    [fetcher],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  return { messages, streaming, send, stop, sessionId: sessionId.current };
}
