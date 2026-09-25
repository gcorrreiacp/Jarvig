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

function stateText(service) {
  if (!service.enabled) return "Off";
  return service.dry_run ? "On (dry run)" : "On";
}

// One block per background service: state, its counts while on, last problem, on/off button.
const SERVICES = [
  {
    key: "analysis",
    label: "Incidents",
    name: "incident analysis",
    counts: [["candidate", "Candidates"], ["analyzed", "Analyzed"], ["discarded", "Discarded"]],
  },
  {
    key: "dispatcher",
    label: "Dispatcher",
    name: "incident dispatcher",
    counts: [["dispatched", "Dispatched"], ["failed", "Failed"]],
  },
  {
    key: "summarizer",
    label: "MR summarizer",
    name: "MR summarizer",
    counts: [["handled", "Summarized"]],
  },
  {
    key: "reviewer",
    label: "MR reviewer",
    name: "MR reviewer",
    counts: [["handled", "Reviewed"]],
  },
];

/** One feature: name, state and its own on/off switch; its counts and last problem while relevant. */
function ServiceRows({ def, service, online, onToggle }) {
  const totals = service.totals ?? {};
  return (
    <>
      <div className="tele__row tele__row--service">
        <dt>
          {def.label}
          {service.beta && <span className="beta-mini" title="In beta (backend/features.json)">BETA</span>}
        </dt>
        <dd><span className="tele__value">{stateText(service)}</span></dd>
        <label className="toggle toggle--compact" title={service.enabled ? `Turn off ${def.name}` : `Enable ${def.name}`}>
          <input
            type="checkbox"
            role="switch"
            checked={service.enabled}
            disabled={!online}
            onChange={(e) => onToggle(def.key, e.target.checked)}
            aria-label={def.name}
          />
          <span className="toggle__track" aria-hidden="true" />
        </label>
      </div>
      {service.enabled && def.counts.map(([key, label]) => (
        <div className="tele__row tele__row--count" key={key}>
          <dt>{label}</dt>
          <dd><span className="tele__value">{totals[key] ?? 0}</span></dd>
        </div>
      ))}
      {service.last_error && <p className="tele__error" role="status">{service.last_error}</p>}
    </>
  );
}

export default function Telemetry({ status, meta, metrics, services, onReset, onToggleService }) {
  const agent = meta.agent;
  const online = status === "online";
  return (
    <aside className="panel tele" aria-label="System status">
      <header className="panel__head">
        <h2>Systems</h2>
        <span className={`dot dot--${status}`} aria-hidden="true" />
      </header>
      {/* Scrolls inside the panel, so a long list never pushes controls off the screen */}
      <div className="tele__body">
        <h3 className="tele__group">Link</h3>
        <dl className="tele__list">
          <Row label="Bridge" value={LINK_TEXT[status]} />
          <Row label="Agent" value={agent?.provider} />
          <Row label="Model" value={agent?.model} />
          <Row label="Round trip" value={metrics.rtt} unit="ms" gauge={<Gauge value={metrics.rtt} max={300} />} />
          <Row label="First token" value={metrics.firstToken} unit="ms" gauge={<Gauge value={metrics.firstToken} max={3000} />} />
          <Row label="Full reply" value={metrics.total} unit="ms" gauge={<Gauge value={metrics.total} max={12000} />} />
          <Row label="Turns" value={metrics.turns} />
        </dl>
        <h3 className="tele__group">Features</h3>
        <dl className="tele__list">
          {SERVICES.map((def) => (
            <ServiceRows key={def.key} def={def} service={services[def.key]} online={online} onToggle={onToggleService} />
          ))}
        </dl>
        <button className="btn btn--ghost" onClick={onReset} disabled={!online}>
          Clear memory
        </button>
      </div>
    </aside>
  );
}
