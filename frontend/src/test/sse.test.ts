import { SSEParser } from "../lib/sse";

test("parses events split across arbitrary chunk boundaries", () => {
  const raw =
    'event: token\ndata: {"text":"Hel"}\n\nevent: token\r\ndata: {"text":"lo"}\r\n\r\n: ping\n\nevent: done\ndata: {"answer":"Hello"}\n\n';
  for (let size = 1; size <= raw.length; size += 7) {
    const p = new SSEParser();
    const out = [];
    for (let i = 0; i < raw.length; i += size) out.push(...p.feed(raw.slice(i, i + size)));
    expect(out.map((e) => e.event)).toEqual(["token", "token", "done"]);
    expect(JSON.parse(out[2]!.data)).toEqual({ answer: "Hello" });
  }
});

test("multi-line data, default event name, id and incomplete tail", () => {
  const p = new SSEParser();
  expect(p.feed("id: 7\ndata: a\ndata: b\n\ndata: tail")).toEqual([
    { event: "message", data: "a\nb", id: "7" },
  ]);
  expect(p.feed("\n\n")).toEqual([{ event: "message", data: "tail", id: undefined }]);
  expect(p.feed("event: x\n\n")).toEqual([]); // no data → no event
});
