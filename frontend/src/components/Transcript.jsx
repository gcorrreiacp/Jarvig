import { useEffect, useRef } from "react";

/** A table sent with a reply, e.g. the timesheet preview: exactly what will be written, day by day. */
function DataTable({ table }) {
  return (
    <figure className="datatable">
      {table.title && <figcaption>{table.title}</figcaption>}
      <div className="datatable__scroll">
        <table>
          <thead>
            <tr>{table.columns.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className={[r.kind && `row--${r.kind}`, r.status && `row--${r.status}`].filter(Boolean).join(" ")}>
                {r.cells.map((c, j) => <td key={j}>{c}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export default function Transcript({ messages, interim, name, speechSupported }) {
  const endRef = useRef(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, interim]);

  return (
    <section className="panel transcript" aria-label="Conversation">
      <header className="panel__head">
        <h2>Conversation</h2>
      </header>
      <div className="transcript__body" aria-live="polite">
        {messages.length === 0 && !interim && (
          <p className="transcript__empty">
            Type a request below{speechSupported ? ", or press Ctrl+Space and speak" : ""}.
          </p>
        )}
        {messages.map((m) => (
          <article key={m.id} className={`line line--${m.role}${m.error ? " line--error" : ""}${m.table ? " line--wide" : ""}`}>
            <span className="line__who">{m.role === "user" ? "You" : name}</span>
            <p className="line__text">
              {m.error ? m.error : m.text}
              {m.streaming && <span className="caret" aria-hidden="true" />}
            </p>
            {m.table && <DataTable table={m.table} />}
            {m.note && <span className="line__note">{m.note}</span>}
          </article>
        ))}
        {interim && (
          <article className="line line--user line--interim">
            <span className="line__who">You</span>
            <p className="line__text">{interim}</p>
          </article>
        )}
        <div ref={endRef} />
      </div>
    </section>
  );
}
