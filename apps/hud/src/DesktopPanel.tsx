import { DesktopOcrView, DesktopView } from "./contracts";

interface Props {
  desktop: DesktopView | null | undefined;
}

function truncate(text: string | null | undefined, max = 240): string {
  if (!text) return "";
  return text.length > max ? text.slice(0, max) + "…" : text;
}

function ocrSourceUrl(o: DesktopOcrView | null | undefined): string | null {
  if (!o || !o.screenshotName) return null;
  return `http://127.0.0.1:7821/screenshots/${encodeURIComponent(o.screenshotName)}?t=${encodeURIComponent(o.updatedAt)}`;
}

// Sanitise an arbitrary identifier (typically an ISO timestamp containing
// `:` and `.`) into a value safe for use inside an HTML id attribute and
// for CSS / `aria-*` lookups.
function safeIdFragment(raw: string | null | undefined): string {
  if (!raw) return "none";
  return raw.replace(/[^A-Za-z0-9_-]/g, "_");
}

export function DesktopPanel({ desktop }: Props) {
  const hasAny =
    !!desktop &&
    (desktop.clipboard ||
      desktop.clipboardWrite ||
      desktop.notification ||
      desktop.foregroundWindow ||
      desktop.focus ||
      desktop.latestScreenshot ||
      desktop.latestOcr);

  const shot = desktop?.latestScreenshot ?? null;
  const shotSrc = shot?.name
    ? `http://127.0.0.1:7821/screenshots/${encodeURIComponent(shot.name)}?t=${encodeURIComponent(shot.updatedAt)}`
    : null;

  const ocr = desktop?.latestOcr ?? null;
  const ocrSrc = ocrSourceUrl(ocr);
  const ocrIdSuffix = safeIdFragment(ocr?.updatedAt);
  const ocrLabelId = `ocr-text-label-${ocrIdSuffix}`;
  const ocrDescId = `ocr-text-desc-${ocrIdSuffix}`;

  return (
    <section className="panel panel-desktop">
      <div className="panel-head">
        <h2>Desktop State</h2>
        <span>Windows-first</span>
      </div>
      {!hasAny && (
        <p className="empty-hint">
          No desktop actions run yet. Try &ldquo;what is in my clipboard?&rdquo;,
          &ldquo;show my current window&rdquo;, &ldquo;copy hello to
          clipboard&rdquo;, or &ldquo;ocr my current window&rdquo;.
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

      {ocr && (
        <article className="desktop-card desktop-ocr">
          <header>
            <strong>Last OCR result</strong>
            <span>
              {ocr.mode} · {ocr.provider || "unavailable"}
            </span>
          </header>
          <dl>
            <div>
              <dt>Lines</dt>
              <dd>{ocr.lineCount}</dd>
            </div>
            <div>
              <dt>Characters</dt>
              <dd>
                {ocr.charCount.toLocaleString()}
                {ocr.truncated ? " (truncated)" : ""}
              </dd>
            </div>
            {ocr.language && (
              <div>
                <dt>Language</dt>
                <dd>{ocr.language}</dd>
              </div>
            )}
            {ocr.averageConfidence !== null && (
              <div>
                <dt>Avg confidence</dt>
                <dd>{(ocr.averageConfidence * 100).toFixed(0)}%</dd>
              </div>
            )}
          </dl>
          {ocrSrc && (
            <img
              className="desktop-screenshot"
              src={ocrSrc}
              alt=""
            />
          )}
          <div id={ocrLabelId} className="desktop-ocr-text-label">
            Extracted text
          </div>
          <div id={ocrDescId} className="sr-only">
            {`${ocr.lineCount} lines, ${ocr.charCount} characters${ocr.truncated ? ", truncated" : ""}`}
          </div>
          <pre
            className="desktop-ocr-text"
            tabIndex={0}
            aria-labelledby={ocrLabelId}
            aria-describedby={ocrDescId}
          >
            {ocr.text || "(no text recognised)"}
          </pre>
          {ocr.truncated && (
            <p className="desktop-ocr-warning" role="status">
              Output was truncated to 64 KB. Full text is in the action result output.
            </p>
          )}
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
