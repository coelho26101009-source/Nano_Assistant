interface SunProps {
  isThinking: boolean;
  statusText: string;
}

export default function Sun({ isThinking, statusText }: SunProps) {
  return (
    <div className="sun-wrapper">
      <div className={`sun-orb ${isThinking ? "sun-thinking" : ""}`}>
        {/* Rotating arcs */}
        <div className="sun-arc sun-arc-1" />
        <div className="sun-arc sun-arc-2" />
        <div className="sun-arc sun-arc-3" />
        {/* Inner rings */}
        <div className="sun-ring sun-ring-1" />
        <div className="sun-ring sun-ring-2" />
        {/* Core */}
        <div className="sun-core" />
        {/* Thinking dots */}
        <div className="sun-pulse-dots">
          <span /><span /><span /><span /><span />
        </div>
      </div>

      <div className="sun-label">NANO ASSISTANT</div>

      {statusText && (
        <div className="sun-status">{statusText}</div>
      )}
    </div>
  );
}
