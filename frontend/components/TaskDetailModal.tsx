import React from "react";

export default function TaskDetailModal({
  visible,
  task,
  events,
  onClose,
}: {
  visible: boolean;
  task: any;
  events: any[];
  onClose: () => void;
}) {
  if (!visible || !task) return null;

  const timeline = (events || []).slice(0, 20).map((event: any) => ({
    label: event.event || "event",
    payload: event.payload || {},
    ts: event.timestamp,
  }));

  const result = task.result || {};
  const steps = Array.isArray(result.steps) ? result.steps : [];
  const permissions = Array.isArray(task.metadata?.permissions) ? task.metadata.permissions : [];
  const verification = steps.map((step: any) => step.result || {}).filter(Boolean);

  return (
    <div className="modal-backdrop" style={{ zIndex: 1300 }}>
      <div className="modal-content" style={{ maxWidth: 980, width: "min(980px, 94vw)", maxHeight: "88vh", overflowY: "auto" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 18 }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 12, letterSpacing: 1, textTransform: "uppercase" }}>Task detail</div>
            <h2 style={{ margin: "8px 0 0" }}>{task.title || "Task"}</h2>
          </div>
          <button type="button" onClick={onClose} style={{ background: "transparent", border: "1px solid var(--border)", color: "var(--text)", borderRadius: 8, padding: "8px 10px", cursor: "pointer" }}>Close</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 18 }}>
          <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 12 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>State</div>
            <div style={{ marginTop: 6, fontWeight: 700 }}>{task.status || "unknown"}</div>
          </div>
          <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 12 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Progress</div>
            <div style={{ marginTop: 6, fontWeight: 700 }}>{task.progress ?? 0}%</div>
          </div>
          <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 12 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Agent</div>
            <div style={{ marginTop: 6, fontWeight: 700 }}>{task.metadata?.agent || "-"}</div>
          </div>
          <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 12 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Retries</div>
            <div style={{ marginTop: 6, fontWeight: 700 }}>{task.retries ?? 0}</div>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1.1fr 0.9fr", gap: 18 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Request</div>
              <div style={{ marginTop: 8, lineHeight: 1.5 }}>{task.description || task.title || "-"}</div>
            </div>

            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Timeline</div>
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                {timeline.length === 0 ? (
                  <div style={{ color: "var(--text-muted)" }}>No events yet.</div>
                ) : (
                  timeline.map((entry, index) => (
                    <div key={`${entry.label}-${index}`} style={{ fontSize: 13, color: "var(--text-muted)" }}>
                      <span style={{ color: "var(--text)", fontWeight: 700 }}>{entry.ts ? new Date(entry.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "-"}</span> — {entry.label}
                    </div>
                  ))
                )}
              </div>
            </div>

            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Tools</div>
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                {steps.length === 0 ? (
                  <div style={{ color: "var(--text-muted)" }}>No tool execution recorded.</div>
                ) : (
                  steps.map((step: any, index: number) => (
                    <div key={`${step.step}-${index}`} style={{ fontSize: 13, color: "var(--text-muted)" }}>
                      • {step.step || "tool"}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Verification</div>
              <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-muted)" }}>
                {verification.length ? verification.map((item: any, index: number) => (
                  <div key={`verify-${index}`}>{item.success ? "Verified" : "Verification failed"} — {item.status || "tool result"}</div>
                )) : "No verification captured."}
              </div>
            </div>

            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Permissions</div>
              <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-muted)" }}>
                {permissions.length ? permissions.map((permission: any, index: number) => (
                  <div key={`${permission.action || "perm"}-${index}`}>{permission.action || permission.capability || "Permission"} — {permission.decision || "pending"}</div>
                )) : "No permissions recorded."}
              </div>
            </div>

            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Errors</div>
              <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-muted)" }}>{task.error || "No errors recorded."}</div>
            </div>

            <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 14 }}>
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 1, color: "var(--text-muted)" }}>Final result</div>
              <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-muted)" }}>{result.status || task.status || "No final result yet."}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
