import { DesktopView } from "./contracts";

interface Props {
  desktop: DesktopView | null | undefined;
}

function truncate(text: string | null | undefined, max = 240): string {
  if (!text) return "";
  return text.length > max ? text.slice(0, max) + "…" : text;
}

export function DesktopPanel({ desktop }: Props) {
  const hasAny =
    !!desktop &&
    (desktop.clipboard ||
      desktop.clipboardWrite ||
      desktop.notification ||
      desktop.foregroundWindow ||
      desktop.focus ||
      desktop.latestScreenshot);

  const shot = desktop?.latestScreenshot ?? null;
  const shotSrc = shot?.name
    ? `http://127.0.0.1:7821/screenshots/${encodeURIComponent(shot.name)}?t=${encodeURIComponent(shot.updatedAt)}`
    : null;

  return (
    <section className="panel panel-desktop">
      <div className="panel-head">
        <h2>Desktop State</h2>
        <span>Windows-first</span>
      </div>
      {!hasAny && (
        <p className="empty-hint">
          No desktop actions run yet. Try &ldquo;what is in my clipboard?&rdquo;,
          &ldquo;show my current window&rdquo;, or &ldquo;copy hello to
          clipboard&rdquo;.
        </p>
      )}

      {desktop?.foregroundWindow && (
        <article className="desktop-card">
          <header>
            <strong>Foreground window</strong>
            <span>{desktop.foregroundWindow.status}</span>
          </header>
          {desktop.foregroundWindow.window ? (
            <dl>
              <div>
                <dt>Title</dt>
                <dd>{desktop.foregroundWindow.window.title || "—"}</dd>
              </div>
              <div>
                <dt>Executable</dt>
                <dd>{desktop.foregroundWindow.window.exe || "—"}</dd>
              </div>
              <div>
                <dt>PID</dt>
                <dd>{desktop.foregroundWindow.window.pid || "—"}</dd>
              </div>
            </dl>
          ) : (
            <p>{desktop.foregroundWindow.summary}</p>
          )}
        </article>
      )}

      {desktop?.clipboard && (
        <article className="desktop-card">
          <header>
            <strong>Clipboard (last read)</strong>
            <span>
              {desktop.clipboard.byteCount} chars
              {desktop.clipboard.truncated ? " · truncated" : ""}
            </span>
          </header>
          <pre className="desktop-clip">{truncate(desktop.clipboard.text) || "(empty)"}</pre>
        </article>
      )}

      {desktop?.clipboardWrite && (
        <article className="desktop-card">
          <header>
            <strong>Clipboard (last write)</strong>
            <span>{desktop.clipboardWrite.byteCount} chars</span>
          </header>
          <p>{desktop.clipboardWrite.summary}</p>
        </article>
      )}

      {desktop?.notification && (
        <article className="desktop-card">
          <header>
            <strong>Last notification</strong>
            <span>{desktop.notification.channel || "—"}</span>
          </header>
          <p>
            <em>{desktop.notification.title}</em>
            {desktop.notification.message ? ": " : ""}
            {desktop.notification.message}
          </p>
        </article>
      )}

      {shot && (
        <article className="desktop-card">
          <header>
            <strong>Last screenshot</strong>
            <span>
              {shot.mode} · {shot.width}×{shot.height}
            </span>
          </header>
          {shotSrc ? (
            <img
              className="desktop-screenshot"
              src={shotSrc}
              alt={`Screenshot (${shot.mode})`}
            />
          ) : (
            <p>{shot.summary}</p>
          )}
          <p className="desktop-screenshot-meta">
            {shot.name} · {shot.byteCount.toLocaleString()} bytes
          </p>
        </article>
      )}

      {desktop?.focus && (
        <article className="desktop-card">
          <header>
            <strong>Last focus attempt</strong>
            <span>{desktop.focus.focused ? "ok" : "refused"}</span>
          </header>
          <p>{desktop.focus.summary}</p>
        </article>
      )}
    </section>
  );
}
