import React from 'react';

interface ThinkingIndicatorProps {
  statusText: string;
}

const ThinkingIndicator: React.FC<ThinkingIndicatorProps> = ({ statusText }) => {
  return (
    <div className="thinking-indicator">
      <div className="thinking-dots">
        <span className="dot dot-1" />
        <span className="dot dot-2" />
        <span className="dot dot-3" />
      </div>
      <span className="thinking-text">{statusText}</span>
    </div>
  );
};

export default ThinkingIndicator;
