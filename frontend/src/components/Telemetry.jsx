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

function incidentText(incidents) {
  if (!incidents.enabled) return "Off";
  return incidents.dry_run ? "On (dry run)" : "On";
}

export default function Telemetry({ status, meta, metrics, incidents, onReset, onToggleIncidents }) {
  const agent = meta.agent;
  const totals = incidents.totals ?? {};
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
        <Row label="Incidents" value={incidentText(incidents)} />
        {incidents.enabled && (
          <>
            <Row label="Candidates" value={totals.candidate ?? 0} />
            <Row label="Analyzed" value={totals.analyzed ?? 0} />
            <Row label="Discarded" value={totals.discarded ?? 0} />
          </>
        )}
      </dl>
      {incidents.last_error && <p className="tele__error" role="status">{incidents.last_error}</p>}
      <button
        className={`btn btn--ghost${incidents.enabled ? " is-on" : ""}`}
        onClick={() => onToggleIncidents(!incidents.enabled)}
        disabled={status !== "online"}
        aria-pressed={incidents.enabled}
      >
        {incidents.enabled ? "Turn off incident analysis" : "Enable incident analysis"}
      </button>
      <button className="btn btn--ghost" onClick={onReset} disabled={status !== "online"}>
        Clear memory
      </button>
    </aside>
  );
}
