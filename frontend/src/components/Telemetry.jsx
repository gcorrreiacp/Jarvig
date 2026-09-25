function Gauge({ value, max }) {
  const pct = value == null ? 0 : Math.min(100, (value / max) * 100);
  return (
    <span className="gauge" aria-hidden="true">
      <span className="gauge__fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

function Row({ label, value, unit, gauge }) {
  return (
    <div className="tele__row">
      <dt>{label}</dt>
      <dd>
        <span className="tele__value">{value ?? "--"}</span>
        {unit && value != null && <span className="tele__unit">{unit}</span>}
      </dd>
      {gauge}
    </div>
  );
}

const LINK_TEXT = { online: "Connected", connecting: "Connecting", offline: "Reconnecting" };

export default function Telemetry({ status, meta, metrics, onReset }) {
  const agent = meta.agent;
  return (
    <aside className="panel tele" aria-label="System status">
      <header className="panel__head">
        <h2>Systems</h2>
        <span className={`dot dot--${status}`} aria-hidden="true" />
      </header>
      <dl className="tele__list">
        <Row label="Bridge" value={LINK_TEXT[status]} />
        <Row label="Agent" value={agent?.provider} />
        <Row label="Model" value={agent?.model} />
        <Row label="Round trip" value={metrics.rtt} unit="ms" gauge={<Gauge value={metrics.rtt} max={300} />} />
        <Row label="First token" value={metrics.firstToken} unit="ms" gauge={<Gauge value={metrics.firstToken} max={3000} />} />
        <Row label="Full reply" value={metrics.total} unit="ms" gauge={<Gauge value={metrics.total} max={12000} />} />
        <Row label="Turns" value={metrics.turns} />
      </dl>
      <button className="btn btn--ghost" onClick={onReset} disabled={status !== "online"}>
        Clear memory
      </button>
    </aside>
  );
}
