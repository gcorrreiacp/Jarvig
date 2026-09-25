// Aura.jsx — React wrapper around aura.js
//
//   const aura = useRef(null);
//   <Aura ref={aura} ttsUrl="/api/tts" style={{ width: 400, height: 400 }} />
//   aura.current.say("Hello, I'm Aura.");

import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { createAura } from './aura.js';

const Aura = forwardRef(function Aura({ className, style, onSpeakingChange, ...options }, ref) {
  const containerRef = useRef(null);
  const auraRef = useRef(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const onSpeakingRef = useRef(onSpeakingChange);
  onSpeakingRef.current = onSpeakingChange;

  // Create once, destroy on unmount
  useEffect(() => {
    const aura = createAura(containerRef.current, optionsRef.current);
    auraRef.current = aura;
    return () => { aura.destroy(); auraRef.current = null; };
  }, []);

  // Pass prop changes (voice, colours, intensity…) to the running orb without recreating it
  useEffect(() => { auraRef.current?.set(options); });

  const turn = useRef(0);
  useImperativeHandle(ref, () => ({
    async say(text) {
      const mine = ++turn.current;   // a newer say() interrupts this one; only the latest reports "done"
      onSpeakingRef.current?.(true);
      try { await auraRef.current?.say(text); }
      finally { if (turn.current === mine) onSpeakingRef.current?.(false); }
    },
    stop: () => auraRef.current?.stop(),
    useMic: () => auraRef.current?.useMic(),
    releaseMic: () => auraRef.current?.releaseMic(),
  }), []);

  return <div ref={containerRef} className={className} style={{ width: 400, height: 400, ...style }} />;
});

export default Aura;
