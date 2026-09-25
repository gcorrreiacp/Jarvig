import { forwardRef, useMemo } from "react";
import Aura from "../aura/Aura.jsx";

const STATE_LABEL = {
  idle: "Standing by",
  listening: "Listening",
  thinking: "Working",
  speaking: "Speaking",
  offline: "No link",
};

// Orb colours per state, matching the HUD accent tokens in styles.css
const ORB_COLORS = {
  idle: { colorA: "#0a4cff", colorB: "#3ff0ff" },
  speaking: { colorA: "#0a4cff", colorB: "#3ff0ff" },
  listening: { colorA: "#1a7dff", colorB: "#8cf0ff" },
  thinking: { colorA: "#a34a00", colorB: "#ffb547" },
  offline: { colorA: "#16252c", colorB: "#5d7b88" },
};

// Built-in voices to try when there is no server TTS: male English voices, best first
const MALE_VOICE = /Daniel|Google UK English Male|Arthur|Oliver|Microsoft Ryan|Microsoft Guy|Aaron|Tom|Alex|Fred|\bMale/i;

const reducedMotion = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * The holographic core: the Aura orb, which moves with the voice, inside rings that react to `state`.
 * The ref is the Aura handle (say, stop, useMic, releaseMic).
 */
const Core = forwardRef(function Core({ state, name, ttsUrl }, auraRef) {
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

  return (
    <div className={`core core--${state}`} role="img" aria-label={`${name}: ${STATE_LABEL[state]}`}>
      <div className="core__stage">
        <Aura
          ref={auraRef}
          className="core__orb"
          style={{ width: undefined, height: undefined }}
          ttsUrl={ttsUrl}
          background="#020a10"
          browserVoice={MALE_VOICE}
          pitch={0.9}
          rate={1}
          intensity={reducedMotion ? 0.3 : 1}
          {...ORB_COLORS[state]}
        />
        <svg viewBox="-210 -210 420 420">
          <defs>
            <filter id="bloom" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3" result="b" />
              <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>

          <g className="ring ring--ticks">
            {ticks.map(({ long, ...t }, i) => (
              <line key={i} {...t} className={long ? "tick tick--long" : "tick"} />
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

          <circle className="ring ring--inner" r="104" filter="url(#bloom)" />
        </svg>
      </div>

      <div className="core__label">
        <span className="core__name">{name}</span>
        <span className="core__state">{STATE_LABEL[state]}</span>
      </div>
    </div>
  );
});

export default Core;
