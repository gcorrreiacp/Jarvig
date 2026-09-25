import { useEffect, useState } from "react";

export default function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="clock">
      <time className="clock__time" dateTime={now.toISOString()}>
        {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
      </time>
      <span className="clock__date">
        {now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}
      </span>
    </div>
  );
}
