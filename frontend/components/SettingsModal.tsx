import React, { useEffect, useState } from "react";

export default function SettingsModal({
  visible,
  onClose,
  onSave,
  audioDevices,
  selectedInput: selectedInputProp,
  selectedOutput: selectedOutputProp,
  inputDeviceId,
  outputDeviceId,
  devices,
}: {
  visible: boolean;
  onClose: () => void;
  onSave: (inputId: number, outputId: number) => void;
  audioDevices?: { inputs: { id: number; name: string }[]; outputs: { id: number; name: string }[] } | null;
  selectedInput?: number | null;
  selectedOutput?: number | null;
  inputDeviceId?: number | null;
  outputDeviceId?: number | null;
  devices?: { inputs: { id: number; name: string }[]; outputs: { id: number; name: string }[] } | null;
}) {
  const resolvedInputDeviceId = selectedInputProp ?? inputDeviceId ?? null;
  const resolvedOutputDeviceId = selectedOutputProp ?? outputDeviceId ?? null;
  const resolvedDevices = audioDevices ?? devices ?? null;

  const [selectedInput, setSelectedInput] = useState<number | null>(resolvedInputDeviceId);
  const [selectedOutput, setSelectedOutput] = useState<number | null>(resolvedOutputDeviceId);

  useEffect(() => {
    setSelectedInput(resolvedInputDeviceId);
    setSelectedOutput(resolvedOutputDeviceId);
  }, [resolvedInputDeviceId, resolvedOutputDeviceId, visible]);

  if (!visible) return null;

  return (
    <div className="modal-backdrop">
      <div className="modal-content">
        <h2>Definições de Áudio</h2>
        <div className="field">
          <label>Dispositivo de Entrada</label>
          <select
            value={selectedInput ?? ""}
            onChange={e => setSelectedInput(Number(e.target.value))}
          >
            <option value="" disabled>
              Selecionar...
            </option>
            {resolvedDevices?.inputs.map(d => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Dispositivo de Saída</label>
          <select
            value={selectedOutput ?? ""}
            onChange={e => setSelectedOutput(Number(e.target.value))}
          >
            <option value="" disabled>
              Selecionar...
            </option>
            {resolvedDevices?.outputs.map(d => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>
        <div className="actions">
          <button onClick={onClose}>Cancelar</button>
          <button
            onClick={() => {
              if (selectedInput !== null && selectedOutput !== null) {
                onSave(selectedInput, selectedOutput);
              }
            }}
          >
            Guardar
          </button>
        </div>
      </div>
    </div>
  );
}
