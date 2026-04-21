import { FormEvent, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { BrowserContextView } from "./contracts";

interface Props {
  context: BrowserContextView | null | undefined;
  degraded: boolean;
  onAfterAction: () => void | Promise<void>;
}

export function BrowserPanel({ context, degraded, onAfterAction }: Props) {
  const [snapUrl, setSnapUrl] = useState("");
  const [snapTitle, setSnapTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onSnapshot = async (e: FormEvent) => {
    e.preventDefault();
    const url = snapUrl.trim();
    if (!url || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await invoke("browser_snapshot", { url, title: snapTitle.trim() || null });
      setSnapUrl("");
      setSnapTitle("");
      await onAfterAction();
    } catch (e) {
      setErr(typeof e === "string" ? e : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onClear = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await invoke("browser_clear", {});
      await onAfterAction();
    } catch (e) {
      setErr(typeof e === "string" ? e : String(e));
    } finally {
      setBusy(false);
    }
  };

  const has = !!context?.url;

  return (
    <section className="panel panel-browser">
      <div className="panel-head">
        <h2>Browser Context</h2>
        <span>{has ? "Context available" : "No context"}</span>
      </div>

      {!has && (
        <p className="empty-hint">
          No current page yet. Ask me to <code>read https://example.com</code> or{" "}
          <code>summarize https://example.com</code>, or push a snapshot below.
        </p>
      )}

      {has && context && (
        <dl className="plan-details">
          <div>
            <dt>URL</dt>
            <dd><code>{context.url}</code></dd>
          </div>
          <div>
            <dt>Title</dt>
            <dd>{context.title || "—"}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd><code>{context.source || "—"}</code></dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{context.updatedAt || "—"}</dd>
          </div>
          <div>
            <dt>Bytes</dt>
            <dd>{context.byteCount}</dd>
          </div>
          {context.textExcerpt && (
            <div>
              <dt>Excerpt</dt>
              <dd>
                <pre className="browser-excerpt">{context.textExcerpt}</pre>
              </dd>
            </div>
          )}
        </dl>
      )}

      <form className="task-form" onSubmit={onSnapshot}>
        <label htmlFor="snap-url">Push a snapshot (explicit, local)</label>
        <div className="task-form-row">
          <input
            id="snap-url"
            type="text"
            placeholder="https://example.com"
            value={snapUrl}
            onChange={(e) => setSnapUrl(e.target.value)}
            disabled={busy || degraded}
            autoComplete="off"
          />
          <input
            type="text"
            placeholder="Optional title"
            value={snapTitle}
            onChange={(e) => setSnapTitle(e.target.value)}
            disabled={busy || degraded}
            autoComplete="off"
          />
          <button type="submit" disabled={busy || degraded || !snapUrl.trim()}>
            {busy ? "Saving…" : "Set current page"}
          </button>
        </div>
        {has && (
          <button
            type="button"
            className="secondary"
            onClick={onClear}
            disabled={busy || degraded}
          >
            Clear context
          </button>
        )}
        {err && <p className="form-error">{err}</p>}
      </form>
    </section>
  );
}
