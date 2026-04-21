import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { VoiceSnapshot } from "./contracts";

interface Props {
  voice: VoiceSnapshot | undefined;
  degraded: boolean;
  onAfterAction: () => void;
  ttsEnabled: boolean;
  onToggleTts: (next: boolean) => void;
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("FileReader failed"));
    reader.onload = () => {
      const result = reader.result as string;
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(blob);
  });
}

export function VoicePanel({ voice, degraded, onAfterAction, ttsEnabled, onToggleTts }: Props) {
  const [editable, setEditable] = useState<string>("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // Keep the editable buffer in sync whenever a new transcript arrives.
  useEffect(() => {
    if (voice?.state === "ready" && voice.transcript !== null) {
      setEditable(voice.transcript ?? "");
    }
    if (voice?.state === "idle") {
      setEditable("");
    }
  }, [voice?.state, voice?.transcript]);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    mediaRecorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const startRecording = useCallback(async () => {
    if (busy || degraded) return;
    setBusy(true);
    setLocalError(null);
    try {
      if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
        throw new Error("Microphone API (getUserMedia) is not available in this environment.");
      }
      await invoke("voice_start");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      mediaRecorderRef.current = rec;
      rec.start();
      onAfterAction();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
      // Make sure the backend does not stay stuck in "recording".
      try { await invoke("voice_reset"); } catch { /* ignore */ }
      stopStream();
    } finally {
      setBusy(false);
    }
  }, [busy, degraded, onAfterAction, stopStream]);

  const stopRecording = useCallback(async () => {
    const rec = mediaRecorderRef.current;
    if (!rec) return;
    setBusy(true);
    setLocalError(null);

    const stopped = new Promise<Blob>((resolve) => {
      rec.onstop = () => {
        const type = rec.mimeType || "audio/webm";
        resolve(new Blob(chunksRef.current, { type }));
      };
    });
    try {
      rec.stop();
      const blob = await stopped;
      const audioBase64 = blob.size > 0 ? await toBase64(blob) : "";
      await invoke("voice_stop", { audioBase64, mime: blob.type || "audio/webm" });
      onAfterAction();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
      try { await invoke("voice_reset"); } catch { /* ignore */ }
    } finally {
      stopStream();
      setBusy(false);
    }
  }, [onAfterAction, stopStream]);

  const submit = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setLocalError(null);
    try {
      await invoke("voice_submit", { transcript: editable, createTask: true });
      setEditable("");
      onAfterAction();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [busy, editable, onAfterAction]);

  const discard = useCallback(async () => {
    setBusy(true);
    setLocalError(null);
    try {
      await invoke("voice_discard");
      setEditable("");
      onAfterAction();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [onAfterAction]);

  const reset = useCallback(async () => {
    setBusy(true);
    setLocalError(null);
    try {
      await invoke("voice_reset");
      setEditable("");
      stopStream();
      onAfterAction();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [onAfterAction, stopStream]);

  const toggleEnabled = useCallback(async () => {
    const next = !(voice?.enabled ?? true);
    setBusy(true);
    try {
      await invoke("voice_enable", { enabled: next });
      onAfterAction();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [voice?.enabled, onAfterAction]);

  const state = voice?.state ?? "idle";
  const enabled = voice?.enabled ?? false;
  const provider = voice?.provider ?? "unknown";
  const recording = state === "recording";
  const ready = state === "ready";
  const transcribing = state === "transcribing";
  const errorState = state === "error";

  const showMic = enabled && !degraded;

  return (
    <section className="panel panel-voice">
      <div className="panel-head">
        <h2>Voice</h2>
        <span className={`voice-state voice-state-${state}`}>
          {state}
        </span>
      </div>

      <div className="voice-controls">
        <label className="inline">
          <input
            type="checkbox"
            checked={enabled}
            onChange={toggleEnabled}
            disabled={busy || degraded}
          />
          Enable microphone
        </label>
        <label className="inline">
          <input
            type="checkbox"
            checked={ttsEnabled}
            onChange={(e) => onToggleTts(e.target.checked)}
          />
          Speak status
        </label>
      </div>

      <p className="voice-hint">
        Push-to-talk only. The microphone opens on press and closes on release.
        Nothing is recorded in the background. Transcription provider:{" "}
        <code>{provider}</code>.
      </p>

      {showMic && (
        <div className="voice-button-row">
          {!recording && !ready && !transcribing && (
            <button
              type="button"
              className="voice-ptt"
              disabled={busy || (state !== "idle" && state !== "error")}
              onMouseDown={startRecording}
              onMouseUp={stopRecording}
              onMouseLeave={() => { if (recording) void stopRecording(); }}
              onTouchStart={(e) => { e.preventDefault(); void startRecording(); }}
              onTouchEnd={(e) => { e.preventDefault(); void stopRecording(); }}
            >
              Hold to talk
            </button>
          )}

          {recording && (
            <button
              type="button"
              className="voice-ptt voice-ptt-live"
              onMouseUp={stopRecording}
              onTouchEnd={(e) => { e.preventDefault(); void stopRecording(); }}
            >
              Recording… release to stop
            </button>
          )}

          {transcribing && (
            <button type="button" className="voice-ptt" disabled>
              Transcribing…
            </button>
          )}

          {(errorState || recording || transcribing) && (
            <button type="button" className="secondary" onClick={reset} disabled={busy}>
              Reset
            </button>
          )}
        </div>
      )}

      {!enabled && (
        <p className="empty-hint">
          Voice is disabled. Tick &ldquo;Enable microphone&rdquo; to opt in.
        </p>
      )}

      {ready && (
        <div className="voice-preview">
          <label htmlFor="voice-transcript">Transcript preview (editable)</label>
          <textarea
            id="voice-transcript"
            rows={3}
            value={editable}
            onChange={(e) => setEditable(e.target.value)}
            disabled={busy}
          />
          <div className="button-row">
            <button type="button" onClick={submit} disabled={busy || !editable.trim()}>
              Submit as task
            </button>
            <button type="button" className="secondary" onClick={discard} disabled={busy}>
              Discard
            </button>
          </div>
        </div>
      )}

      {errorState && voice?.error && (
        <p className="form-error">{voice.error}</p>
      )}
      {localError && <p className="form-error">{localError}</p>}
    </section>
  );
}
