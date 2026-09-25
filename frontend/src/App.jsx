import { useEffect, useRef, useState } from "react";
import { useBridge } from "./hooks/useBridge.js";
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

  const bridge = useBridge({ onReply: (text) => voiceRef.current && speakRef.current?.(text) });
  const speech = useSpeech({ onFinal: (text) => bridge.send(text) });
  speakRef.current = speech.speak;

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
    <div className={`hud hud--${state}`}>
      <div className="hud__grid" aria-hidden="true" />
      <header className="hud__top">
        <h1 className="brand">{name}</h1>
        <Clock />
      </header>

      <main className="hud__main">
        <Telemetry status={bridge.status} meta={bridge.meta} metrics={bridge.metrics} onReset={bridge.reset} />
        <div className="hud__center">
          <Core state={state} name={name} />
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
          setVoiceReplies={setVoiceReplies}
        />
      </footer>
    </div>
  );
}
