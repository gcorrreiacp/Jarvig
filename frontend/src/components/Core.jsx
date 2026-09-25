import { useMemo } from "react";

const STATE_LABEL = {
  idle: "Standing by",
  listening: "Listening",
  thinking: "Working",
  speaking: "Speaking",
  offline: "No link",
};

/** The holographic core. Every ring reacts to `state`. */
export default function Core({ state, name }) {
  const ticks = useMemo(
    () =>
      Array.from({ length: 120 }, (_, i) => {
        const a = (i / 120) * Math.PI * 2;
        const long = i % 10 === 0;
        const r1 = 190, r2 = long ? 176 : 184;
        return { x1: Math.cos(a) * r1, y1: Math.sin(a) * r1, x2: Math.cos(a) * r2, y2: Math.sin(a) * r2, long };
      }),
    []
  );

  const spokes = useMemo(
    () =>
      Array.from({ length: 12 }, (_, i) => {
        const a = (i / 12) * Math.PI * 2 + Math.PI / 12;
        return { x1: Math.cos(a) * 76, y1: Math.sin(a) * 76, x2: Math.cos(a) * 104, y2: Math.sin(a) * 104 };
      }),
    []
  );

  return (
    <div className={`core core--${state}`} role="img" aria-label={`${name}: ${STATE_LABEL[state]}`}>
      <svg viewBox="-210 -210 420 420">
        <defs>
          <radialGradient id="coreGlow">
            <stop offset="0%" stopColor="var(--holo-hot)" stopOpacity="0.95" />
            <stop offset="45%" stopColor="var(--holo)" stopOpacity="0.45" />
            <stop offset="100%" stopColor="var(--holo)" stopOpacity="0" />
          </radialGradient>
          <filter id="bloom" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        <g className="ring ring--ticks">
          {ticks.map((t, i) => (
            <line key={i} {...t} className={t.long ? "tick tick--long" : "tick"} />
          ))}
        </g>

        <circle className="ring ring--segments" r="162" />
        <circle className="ring ring--hair" r="146" />
        <circle className="ring ring--dots" r="132" />

        <g className="ring ring--arc">
          {/* invisible full circle keeps the rotation centred */}
          <circle r="118" stroke="none" />
          <path d="M 0 -118 A 118 118 0 0 1 102 59" />
          <path d="M -102 59 A 118 118 0 0 1 -60 -101" />
        </g>

        <g className="ring ring--spokes">
          {spokes.map((s, i) => <line key={i} {...s} />)}
        </g>

        <circle className="ring ring--inner" r="90" filter="url(#bloom)" />
        <circle className="pulse" r="70" fill="url(#coreGlow)" />
        <circle className="ring ring--eye" r="46" filter="url(#bloom)" />
      </svg>

      <div className="core__label">
        <span className="core__name">{name}</span>
        <span className="core__state">{STATE_LABEL[state]}</span>
      </div>
    </div>
  );
}
