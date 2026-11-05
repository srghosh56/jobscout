import { useState } from "react";
import { AnswerText } from "../components/CitationChip";
import { Feedback } from "../components/Feedback";
import { type ToolEvent, useChatStream } from "../hooks/useChatStream";

function ToolPanel({ t }: { t: ToolEvent }) {
  const result = t.result as Record<string, unknown> | undefined;
  return (
    <details className="tool">
      <summary>
        🔧 {t.name}
        {t.seconds != null && ` (${t.seconds.toFixed(2)} s)`}
      </summary>
      {t.name === "sql_query" && result ? (
        <>
          <pre className="sql">{String(result.sql ?? (t.args as { sql?: string }).sql ?? "")}</pre>
          {result.error ? (
            <p role="alert">{String(result.error)}</p>
          ) : (
            <p className="muted">{String(result.row_count)} rows</p>
          )}
        </>
      ) : t.name === "search_postings" && result ? (
        <ul>
          {((result.results as { id: string; title: string; city: string | null }[]) ?? []).map(
            (r) => (
              <li key={r.id}>
                <a href={`/postings/${r.id}`}>{r.title}</a> · {r.city}
              </li>
            ),
          )}
        </ul>
      ) : (
        <pre>{JSON.stringify(t.args, null, 2)}</pre>
      )}
    </details>
  );
}

export function AssistantPage() {
  const { messages, streaming, send, stop, sessionId } = useChatStream();
  const [input, setInput] = useState("");
  return (
    <section>
      <h1>Assistant</h1>
      <p className="muted">
        Ask in any language, e.g. “Which ML roles in Munich ask for at most 3 years and mention
        RAG?” Answers cite postings; Blue Card statements are indicative, not legal advice.
      </p>
      <div className="chat" aria-live="polite">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <p key={i} className="msg user">
              {m.text}
            </p>
          ) : (
            <div key={i} className="msg assistant">
              {m.tools.map((t) => (
                <ToolPanel key={t.id} t={t} />
              ))}
              {m.pending && !m.text && <p className="muted">Thinking…</p>}
              <AnswerText text={m.text} citations={m.citations} />
              {m.warnings.map((w) => (
                <p key={w} className="warning" role="note">
                  ⚠️ {w}
                </p>
              ))}
              {m.error && <p role="alert">{m.error}</p>}
              {!m.pending && m.text && (
                <Feedback
                  question={m.question ?? ""}
                  answer={m.text}
                  traceId={m.traceId}
                  messageId={m.messageId}
                  sessionId={sessionId}
                />
              )}
            </div>
          ),
        )}
      </div>
      <form
        className="searchbar"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
          setInput("");
        }}
      >
        <label className="sr-only" htmlFor="ask">
          Question
        </label>
        <input
          id="ask"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about jobs in Germany…"
        />
        {streaming ? (
          <button type="button" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit">Ask</button>
        )}
      </form>
    </section>
  );
}
