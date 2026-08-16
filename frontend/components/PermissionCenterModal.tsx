import React from "react";

export type PermissionRequest = {
  id?: string;
  request_id?: string;
  action?: string;
  capability?: string;
  task_id?: string;
  target?: string;
  reason?: string;
  risk?: string;
  status?: string;
  args?: Record<string, any>;
};

export default function PermissionCenterModal({
  visible,
  requests,
  policies,
  onClose,
  onResolve,
}: {
  visible: boolean;
  requests: PermissionRequest[];
  policies: any[];
  onClose: () => void;
  onResolve: (requestId: string, decision: "deny" | "allow_once" | "allow_for_task") => void;
}) {
  if (!visible) return null;

  const sanitizeArgs = (args: Record<string, any> | undefined) => {
    if (!args) return [];
    return Object.entries(args)
      .filter(([key]) => !/secret|token|password|key|credential/i.test(key))
      .slice(0, 6)
      .map(([key, value]) => ({ key, value }));
  };

  return (
    <div className="modal-backdrop" style={{ zIndex: 1200 }}>
      <div className="modal-content" style={{ maxWidth: 980, width: "min(980px, 92vw)", maxHeight: "86vh", overflowY: "auto" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 12, letterSpacing: 1, textTransform: "uppercase" }}>Permission center</div>
            <h2 style={{ margin: "8px 0 0" }}>Nano permission requests</h2>
          </div>
          <button type="button" onClick={onClose} style={{ background: "transparent", border: "1px solid var(--border)", color: "var(--text)", borderRadius: 8, padding: "8px 10px", cursor: "pointer" }}>Close</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 0.9fr", gap: 18 }}>
          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 12, letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Pending permissions</div>
            {requests.length === 0 ? (
              <div style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 16, color: "var(--text-muted)" }}>
                No pending permissions.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {requests.map((request) => {
                  const requestId = request.id || request.request_id || "unknown";
                  const risk = (request.risk || "medium").toUpperCase();
                  const details = sanitizeArgs(request.args);
                  return (
                    <div key={requestId} style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 12, padding: 16 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                        <div style={{ fontSize: 12, letterSpacing: 1, textTransform: "uppercase", color: "var(--text-muted)" }}>Task {request.task_id || "-"}</div>
                        <div style={{ fontSize: 12, color: risk === "CRITICAL" ? "#ff7a7a" : risk === "HIGH" ? "#ffcd70" : "var(--text-muted)", fontWeight: 700 }}>{risk}</div>
                      </div>

                      <div style={{ marginTop: 12, fontSize: 20, fontWeight: 700 }}>{request.action || request.capability || "Permission request"}</div>

                      <div style={{ marginTop: 12, display: "grid", gap: 8, fontSize: 13 }}>
                        <div><strong>Action:</strong> {request.action || request.capability || "-"}</div>
                        <div><strong>Target:</strong> {request.target || "-"}</div>
                        <div><strong>Reason:</strong> {request.reason || "Requested by current task."}</div>
                        <div><strong>Task:</strong> {request.task_id || "-"}</div>
                      </div>

                      {details.length > 0 && (
                        <div style={{ marginTop: 12 }}>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>Arguments</div>
                          <div style={{ marginTop: 6, display: "grid", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
                            {details.map(({ key, value }) => (
                              <div key={key}><strong style={{ color: "var(--text)" }}>{key}</strong>: {String(value).slice(0, 120)}</div>
                            ))}
                          </div>
                        </div>
                      )}

                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
                        <button type="button" onClick={() => onResolve(requestId, "deny")} style={{ background: "#3a1d1d", border: "1px solid #7a2d2d", color: "#ffd7d7", borderRadius: 8, padding: "10px 14px", cursor: "pointer", fontWeight: 700 }}>DENY</button>
                        <button type="button" onClick={() => onResolve(requestId, "allow_once")} style={{ background: "#183327", border: "1px solid #2b7d5d", color: "#d9fbe8", borderRadius: 8, padding: "10px 14px", cursor: "pointer", fontWeight: 700 }}>ALLOW ONCE</button>
                        <button type="button" onClick={() => onResolve(requestId, "allow_for_task")} style={{ background: "#24345a", border: "1px solid #4567b8", color: "#dfe9ff", borderRadius: 8, padding: "10px 14px", cursor: "pointer", fontWeight: 700 }}>ALLOW FOR TASK</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div>
            <div style={{ color: "var(--text-muted)", fontSize: 12, letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Policies</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(policies || []).slice(0, 8).map((policy: any) => (
                <div key={policy.capability || policy.action} style={{ background: "var(--bg-sidebar)", border: "1px solid var(--border)", borderRadius: 10, padding: 10 }}>
                  <div style={{ fontWeight: 700 }}>{policy.capability || policy.action || "Capability"}</div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>Risk: {policy.risk || "low"}</div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Decision: {policy.decision || "allow"}</div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Scope: {policy.scope || "workspace"}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
