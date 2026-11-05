import { act, renderHook, waitFor } from "@testing-library/react";
import { useChatStream } from "../hooks/useChatStream";

function streamOf(chunks: string[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      chunks.forEach((x) => c.enqueue(enc.encode(x)));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

test("assembles tool events, tokens, reset and the final checked answer", async () => {
  localStorage.setItem("jobscout.apiKey", "js_abcdefgh_test");
  const sse = [
    'event: tool_call\ndata: {"id":"c1","name":"search_postings","args":{"query":"RAG"}}\n\n',
    'event: tool_result\ndata: {"id":"c1","name":"search_postings","result":{"results":[]},"seconds":0.1}\n\n',
    'event: token\ndata: {"text":"Draft [an-ffff',
    'ffffff]"}\n\nevent: reset\ndata: {}\n\n',
    'event: token\ndata: {"text":"Final "}\n\nevent: citation\ndata: {"ids":["an-0000000001"]}\n\n',
    'event: done\ndata: {"answer":"Final [an-0000000001]","citations":["an-0000000001"],"warnings":[],"trace_id":"t","message_id":"m"}\n\n',
  ];
  const fetcher = async () => streamOf(sse);
  const { result } = renderHook(() => useChatStream(fetcher as unknown as typeof fetch));
  await act(async () => {
    await result.current.send("RAG jobs?");
  });
  await waitFor(() => expect(result.current.streaming).toBe(false));
  const [user, bot] = result.current.messages;
  expect(user!.text).toBe("RAG jobs?");
  expect(bot!.text).toBe("Final [an-0000000001]");
  expect(bot!.citations).toEqual(["an-0000000001"]);
  expect(bot!.tools[0]).toMatchObject({ name: "search_postings", seconds: 0.1 });
  expect(bot!.traceId).toBe("t");
});

test("surfaces rate limiting as a friendly error", async () => {
  localStorage.setItem("jobscout.apiKey", "js_abcdefgh_test");
  const fetcher = async () => new Response("", { status: 429 });
  const { result } = renderHook(() => useChatStream(fetcher as unknown as typeof fetch));
  await act(async () => {
    await result.current.send("hi");
  });
  expect(result.current.messages[1]!.error).toMatch(/Too many requests/);
});
