import { useState } from "react";
import { api } from "../api/client";

interface Props {
  question: string;
  answer: string;
  traceId?: string;
  messageId?: string;
  sessionId?: string;
}

export function Feedback({ question, answer, traceId, messageId, sessionId }: Props) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<"idle" | "sent" | "error">("idle");

  async function submit(r: 1 | -1, c?: string) {
    setRating(r);
    const { error } = await api.POST("/api/feedback", {
      body: {
        question,
        answer,
        rating: r,
        comment: c || null,
        trace_id: traceId ?? null,
        message_id: messageId ?? null,
        session_id: sessionId ?? null,
      },
    });
    setStatus(error ? "error" : "sent");
  }

  return (
    <div className="feedback" aria-label="Rate this answer">
      <button
        type="button"
        aria-pressed={rating === 1}
        onClick={() => submit(1)}
        aria-label="Helpful"
      >
        👍
      </button>
      <button
        type="button"
        aria-pressed={rating === -1}
        onClick={() => setRating(-1)}
        aria-label="Not helpful"
      >
        👎
      </button>
      {rating === -1 && status !== "sent" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit(-1, comment);
          }}
        >
          <label>
            What was wrong?
            <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} />
          </label>
          <button type="submit">Send</button>
        </form>
      )}
      {status === "sent" && <span role="status">Thanks for the feedback!</span>}
      {status === "error" && <span role="alert">Could not send feedback.</span>}
    </div>
  );
}
