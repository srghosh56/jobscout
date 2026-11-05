import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { Feedback } from "../components/Feedback";

test("thumbs down asks for a comment and posts it with the trace id", async () => {
  localStorage.setItem("jobscout.apiKey", "js_abcdefgh_test");
  const fetchMock = vi.fn(
    async (req: Request) =>
      req &&
      new Response(JSON.stringify({ id: 1, status: "recorded" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Feedback question="q?" answer="a" traceId="trace-1" messageId="m1" sessionId="s1" />);
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("Not helpful"));
  await user.type(screen.getByLabelText("What was wrong?"), "wrong city");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Thanks");
  const req = fetchMock.mock.calls[0]![0];
  expect(req.headers.get("X-API-Key")).toBe("js_abcdefgh_test");
  expect(await req.json()).toMatchObject({
    rating: -1,
    comment: "wrong city",
    trace_id: "trace-1",
    message_id: "m1",
  });
  vi.unstubAllGlobals();
});
