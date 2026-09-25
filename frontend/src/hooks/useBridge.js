import { useCallback, useEffect, useRef, useState } from "react";

function sessionId() {
  try {
    let id = sessionStorage.getItem("jarvig-session");
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem("jarvig-session", id);
    }
    return id;
  } catch {
    return Math.random().toString(36).slice(2);
  }
}

function bridgeUrl(session) {
  const explicit = import.meta.env.VITE_BRIDGE_WS;
  const base = explicit || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  return `${base}${base.includes("?") ? "&" : "?"}session=${encodeURIComponent(session)}`;
}

/** REST URL on the bridge; follows VITE_BRIDGE_WS when the bridge lives on another host. */
export function apiUrl(path) {
  const explicit = import.meta.env.VITE_BRIDGE_WS;
  return explicit ? new URL(path, explicit.replace(/^ws/, "http")).href : path;
}

/**
 * WebSocket link to the FastAPI bridge.
 * Handles reconnects, streaming tokens, cancel/reset and latency probes.
 */
export function useBridge({ onReply } = {}) {
  const [status, setStatus] = useState("connecting"); // connecting | online | offline
  const [meta, setMeta] = useState({ assistant: "J.A.R.V.I.G.", agent: null, tts: false });
  const [services, setServices] = useState({
    analysis: { enabled: false, totals: {} },
    dispatcher: { enabled: false, totals: {} },
  });
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [metrics, setMetrics] = useState({ rtt: null, firstToken: null, total: null, turns: 0 });

  const wsRef = useRef(null);
  const retryRef = useRef(0);
  const onReplyRef = useRef(onReply);
  onReplyRef.current = onReply;
  const session = useRef(sessionId()).current;

  const patchLast = (id, fn) =>
    setMessages((list) => list.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)));

  useEffect(() => {
    let closedByUs = false;
    let pingTimer;
    let retryTimer;

    const connect = () => {
      setStatus("connecting");
      const ws = new WebSocket(bridgeUrl(session));
      wsRef.current = ws;

      ws.onopen = () => {
        retryRef.current = 0;
        setStatus("online");
        pingTimer = setInterval(() => {
          if (ws.readyState === 1) ws.send(JSON.stringify({ type: "ping", t: performance.now() }));
        }, 5000);
        ws.send(JSON.stringify({ type: "ping", t: performance.now() }));
      };

      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
          case "hello":
            setMeta({ assistant: msg.assistant, agent: msg.agent, tts: Boolean(msg.tts) });
            setMetrics((m) => ({ ...m, turns: msg.turns ?? 0 }));
            document.title = msg.assistant;
            break;
          case "service":
            setServices((all) => ({ ...all, [msg.service]: msg }));
            break;
          case "pong":
            setMetrics((m) => ({ ...m, rtt: Math.round(performance.now() - msg.t) }));
            break;
          case "start":
            setBusy(true);
            setMessages((list) => [...list, { id: msg.id, role: "assistant", text: "", streaming: true }]);
            break;
          case "token":
            patchLast(msg.id, (m) => ({ text: m.text + msg.text }));
            break;
          case "done":
            setBusy(false);
            patchLast(msg.id, () => ({ text: msg.text, streaming: false }));
            // Replies the bridge writes itself ("local") have no agent timings; keep the last ones.
            setMetrics((m) => (msg.local
              ? { ...m, turns: msg.turns }
              : { ...m, firstToken: msg.first_token_ms, total: msg.total_ms, turns: msg.turns }));
            onReplyRef.current?.(msg.text);
            break;
          case "cancelled":
            setBusy(false);
            patchLast(msg.id, (m) => ({ streaming: false, note: "Stopped" }));
            break;
          case "error":
            setBusy(false);
            patchLast(msg.id, () => ({ streaming: false, error: msg.message }));
            break;
          case "reset_ok":
            setMessages([]);
            setMetrics((m) => ({ ...m, turns: 0, firstToken: null, total: null }));
            break;
        }
      };

      ws.onclose = () => {
        clearInterval(pingTimer);
        setBusy(false);
        if (closedByUs) return;
        setStatus("offline");
        const delay = Math.min(10000, 500 * 2 ** retryRef.current++);
        retryTimer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closedByUs = true;
      clearInterval(pingTimer);
      clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [session]);

  const raw = (payload) => {
    const ws = wsRef.current;
    if (ws?.readyState !== 1) return false;
    ws.send(JSON.stringify(payload));
    return true;
  };

  const send = useCallback((text) => {
    const clean = text.trim();
    if (!clean) return false;
    const ok = raw({ type: "user_message", text: clean });
    if (ok) setMessages((list) => [...list, { id: `u-${Date.now()}`, role: "user", text: clean }]);
    return ok;
  }, []);

  const cancel = useCallback(() => raw({ type: "cancel" }), []);
  const reset = useCallback(() => raw({ type: "reset" }), []);
  const toggleService = useCallback((service, enabled) => raw({ type: "toggle", service, enabled }), []);

  return { status, meta, messages, busy, metrics, services, send, cancel, reset, toggleService, session };
}
