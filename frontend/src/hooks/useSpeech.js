import { useCallback, useEffect, useRef, useState } from "react";

const Recognition = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

// Strip markdown so speech sounds natural, and say the name as a word, not letters.
const speakable = (t) =>
  t.replace(/```[\s\S]*?```/g, " code block omitted. ").replace(/[*_#`>]/g, "").replace(/\[(.*?)\]\(.*?\)/g, "$1")
    .replace(/J\.A\.R\.V\.I\.G\./g, "Jarvig");


/**
 * Browser speech-to-text, and text-to-speech through the Aura orb (`voice` is its ref),
 * which picks the voice (server TTS, ElevenLabs or the built-in one) and animates with it.
 * Recognition works best in Chrome/Edge.
 */
export function useSpeech({ onFinal, voice } = {}) {
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState(null);
  const recRef = useRef(null);
  const turn = useRef(0);
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
    window.speechSynthesis?.getVoices(); // voices load lazily; ask early so the first reply has one
    return () => rec.abort();
  }, []);

  const stopSpeaking = useCallback(() => {
    turn.current++;
    voice?.current?.stop();
    setSpeaking(false);
  }, [voice]);

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

  const speak = useCallback(async (text) => {
    if (!voice?.current || !text) return;
    const mine = ++turn.current; // a newer reply interrupts this one; only the latest clears "speaking"
    setSpeaking(true);
    try {
      await voice.current.say(speakable(text));
    } catch (e) {
      // Try first: Chrome often allows speech on load for sites you use a lot. Only when the browser
      // refuses (no click or key press yet) hold it and say it on the first interaction.
      if (e?.name === "NotAllowedError") {
        if (turn.current === mine) holdForGesture(text);
      } else {
        setError(`Voice: ${e.message}`);
      }
    } finally {
      if (turn.current === mine) setSpeaking(false);
    }
  }, [voice]);

  const holdForGesture = (text) => {
    const queued = turn.current;
    const later = (e) => {
      window.removeEventListener("pointerdown", later);
      window.removeEventListener("keydown", later);
      // Not when that first key press is push-to-talk (the mic would hear it),
      // and not if speech was stopped meanwhile (e.g. "Read replies aloud" switched off).
      if (!(e.ctrlKey && e.code === "Space") && turn.current === queued) speakRef.current?.(text);
    };
    window.addEventListener("pointerdown", later, { once: true });
    window.addEventListener("keydown", later, { once: true });
  };
  const speakRef = useRef(speak);
  speakRef.current = speak;

  return {
    supported: Boolean(Recognition),
    listening, speaking, interim, error,
    listen, stopListening, speak, stopSpeaking,
  };
}
