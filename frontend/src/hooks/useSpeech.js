import { useCallback, useEffect, useRef, useState } from "react";

const Recognition = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

function pickVoice() {
  const voices = window.speechSynthesis?.getVoices() ?? [];
  const prefs = [/en-GB/i, /en-AU/i, /en-US/i, /^en/i];
  for (const p of prefs) {
    const male = voices.find((v) => p.test(v.lang) && /male|daniel|arthur|george|oliver/i.test(v.name));
    if (male) return male;
    const any = voices.find((v) => p.test(v.lang));
    if (any) return any;
  }
  return voices[0] ?? null;
}

// Strip markdown so speech sounds natural, and say the name as a word, not letters.
const speakable = (t) =>
  t.replace(/```[\s\S]*?```/g, " code block omitted. ").replace(/[*_#`>]/g, "").replace(/\[(.*?)\]\(.*?\)/g, "$1")
    .replace(/J\.A\.R\.V\.I\.G\./g, "Jarvig");

// Browsers refuse to speak before the first click or key press on the page.
const hasUserActivation = () => navigator.userActivation?.hasBeenActive ?? true;

/** Browser speech-to-text and text-to-speech. Works best in Chrome/Edge. */
export function useSpeech({ onFinal } = {}) {
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState(null);
  const recRef = useRef(null);
  const onFinalRef = useRef(onFinal);
  onFinalRef.current = onFinal;

  useEffect(() => {
    if (!Recognition) return;
    const rec = new Recognition();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    rec.onresult = (e) => {
      let finalText = "";
      let partial = "";
      for (const r of e.results) (r.isFinal ? (finalText += r[0].transcript) : (partial += r[0].transcript));
      setInterim(partial || finalText);
      if (finalText) {
        setInterim("");
        onFinalRef.current?.(finalText);
      }
    };
    rec.onerror = (e) => {
      if (e.error !== "no-speech" && e.error !== "aborted") setError(`Microphone: ${e.error}`);
    };
    rec.onend = () => setListening(false);
    recRef.current = rec;
    window.speechSynthesis?.getVoices();
    return () => rec.abort();
  }, []);

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }, []);

  const listen = useCallback(() => {
    if (!recRef.current) return;
    stopSpeaking();
    setError(null);
    try {
      recRef.current.start();
      setListening(true);
    } catch {
      /* already started */
    }
  }, [stopSpeaking]);

  const stopListening = useCallback(() => recRef.current?.stop(), []);

  const speak = useCallback((text) => {
    if (!window.speechSynthesis || !text) return;
    if (!hasUserActivation()) {
      // e.g. the greeting on page load: say it on the first interaction instead of losing it.
      const later = (e) => {
        window.removeEventListener("pointerdown", later);
        window.removeEventListener("keydown", later);
        // Not when that first key press is push-to-talk: the mic would hear the greeting.
        if (!(e.ctrlKey && e.code === "Space")) speakRef.current?.(text);
      };
      window.addEventListener("pointerdown", later, { once: true });
      window.addEventListener("keydown", later, { once: true });
      return;
    }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(speakable(text));
    const v = pickVoice();
    if (v) u.voice = v;
    u.rate = 1.02;
    u.pitch = 0.9;
    u.onstart = () => setSpeaking(true);
    u.onend = u.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(u);
  }, []);
  const speakRef = useRef(speak);
  speakRef.current = speak;

  return {
    supported: Boolean(Recognition),
    listening, speaking, interim, error,
    listen, stopListening, speak, stopSpeaking,
  };
}
