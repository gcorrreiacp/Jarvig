import { useState } from "react";

const MicIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
);
const SendIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h14M13 6l6 6-6 6" /></svg>
);
const StopIcon = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" /></svg>
);

export default function CommandBar({ onSend, onCancel, busy, online, speech, voiceReplies, setVoiceReplies }) {
  const [text, setText] = useState("");

  const submit = (e) => {
    e?.preventDefault();
    if (onSend(text)) setText("");
  };

  return (
    <div className="command">
      <form className="command__form" onSubmit={submit}>
        <label htmlFor="cmd" className="sr-only">Message</label>
        <input
          id="cmd"
          autoComplete="off"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && onCancel()}
          placeholder={online ? "Ask anything" : "Waiting for the bridge"}
          disabled={!online}
        />
        {busy ? (
          <button type="button" className="btn btn--icon btn--warn" onClick={onCancel} aria-label="Stop reply">
            <StopIcon />
          </button>
        ) : (
          <button type="submit" className="btn btn--icon" disabled={!online || !text.trim()} aria-label="Send">
            <SendIcon />
          </button>
        )}
      </form>

      {speech.supported && (
        <button
          className={`btn btn--mic${speech.listening ? " is-live" : ""}`}
          onClick={speech.listening ? speech.stopListening : speech.listen}
          disabled={!online}
          aria-pressed={speech.listening}
          aria-label={speech.listening ? "Stop listening" : "Talk"}
          title="Ctrl+Space"
        >
          <MicIcon />
        </button>
      )}

      <label className="toggle">
        <input type="checkbox" checked={voiceReplies} onChange={(e) => setVoiceReplies(e.target.checked)} />
        <span className="toggle__track" aria-hidden="true" />
        Read replies aloud
      </label>

      {speech.error && <p className="command__error" role="alert">{speech.error}</p>}
    </div>
  );
}
