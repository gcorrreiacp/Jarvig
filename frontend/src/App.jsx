import { useEffect, useRef, useState } from "react";
import { apiUrl, useBridge } from "./hooks/useBridge.js";
import { useSpeech } from "./hooks/useSpeech.js";
import Core from "./components/Core.jsx";
import Transcript from "./components/Transcript.jsx";
import Telemetry from "./components/Telemetry.jsx";
import CommandBar from "./components/CommandBar.jsx";
import Clock from "./components/Clock.jsx";

export default function App() {
  const [voiceReplies, setVoiceReplies] = useState(true);
  const voiceRef = useRef(voiceReplies);
  voiceRef.current = voiceReplies;
  const speakRef = useRef(null);
  const auraRef = useRef(null);

  const bridge = useBridge({ onReply: (text) => voiceRef.current && speakRef.current?.(text) });
  const speech = useSpeech({ onFinal: (text) => bridge.send(text), voice: auraRef });
  speakRef.current = speech.speak;

  // While listening, the orb moves with the user's voice
  useEffect(() => {
    if (!speech.listening) return;
    auraRef.current?.useMic().catch(() => { /* no mic permission: the orb just stays calm */ });
    return () => auraRef.current?.releaseMic();
  }, [speech.listening]);

  // Ctrl+Space: push to talk. Esc: stop everything.
  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.code === "Space") {
        e.preventDefault();
        speech.listening ? speech.stopListening() : speech.listen();
      } else if (e.key === "Escape") {
        bridge.cancel();
        speech.stopSpeaking();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [speech, bridge]);

  const state =
    bridge.status !== "online" ? "offline"
    : speech.listening ? "listening"
    : bridge.busy ? "thinking"
    : speech.speaking ? "speaking"
    : "idle";

  const name = bridge.meta.assistant;

  return (
    <div className={`hud hud--${state}${bridge.beta ? " hud--beta" : ""}`}>
      <div className="hud__grid" aria-hidden="true" />
      <header className="hud__top">
        <h1 className="brand">
          {name}
          {bridge.beta && <span className="beta-tag">BETA · {bridge.beta}</span>}
        </h1>
        <Clock />
      </header>

      <main className="hud__main">
        <Telemetry
          status={bridge.status}
          meta={bridge.meta}
          metrics={bridge.metrics}
          services={bridge.services}
          onReset={bridge.reset}
          onToggleService={bridge.toggleService}
        />
        <div className="hud__center">
          <Core
            ref={auraRef}
            state={state}
            name={name}
            beta={Boolean(bridge.beta)}
            ttsUrl={bridge.meta.tts ? apiUrl("/api/tts") : null}
          />
          {speech.waitingForGesture && voiceReplies && (
            // Browsers only allow speech after a click or key press; any click on the page plays it.
            <button className="voice-hint" type="button">🔊 Click to hear {name}</button>
          )}
        </div>
        <Transcript
          messages={bridge.messages}
          interim={speech.interim}
          name={name}
          speechSupported={speech.supported}
        />
      </main>

      <footer className="hud__bottom">
        <CommandBar
          onSend={bridge.send}
          onCancel={() => { bridge.cancel(); speech.stopSpeaking(); }}
          busy={bridge.busy}
          online={bridge.status === "online"}
          speech={speech}
          voiceReplies={voiceReplies}
          setVoiceReplies={(on) => {
            setVoiceReplies(on);
            if (!on) speech.stopSpeaking(); // cut the voice now, not after the current reply
          }}
        />
      </footer>
    </div>
  );
}
