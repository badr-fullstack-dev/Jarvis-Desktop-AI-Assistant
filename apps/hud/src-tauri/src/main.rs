#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;

const BRIDGE_BASE: &str = "http://127.0.0.1:7821";
const BRIDGE_TIMEOUT_SECS: u64 = 5;
const VOICE_TIMEOUT_SECS: u64 = 180;

#[derive(Serialize)]
struct HealthStatus {
    status: &'static str,
    message: &'static str,
}

#[tauri::command]
fn health() -> HealthStatus {
    HealthStatus {
        status: "ok",
        message: "Jarvis HUD bridge scaffold is online.",
    }
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(BRIDGE_TIMEOUT_SECS))
        .build()
        .map_err(|e| format!("http client init failed: {e}"))
}

fn voice_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(VOICE_TIMEOUT_SECS))
        .build()
        .map_err(|e| format!("http client init failed: {e}"))
}

async fn bridge_get(path: &str) -> Result<Value, String> {
    let url = format!("{BRIDGE_BASE}{path}");
    let resp = client()?
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("bridge unavailable: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("bridge {} -> HTTP {}", path, resp.status()));
    }
    resp.json::<Value>()
        .await
        .map_err(|e| format!("bridge decode error: {e}"))
}

#[tauri::command]
async fn get_hud_state() -> Result<Value, String> {
    bridge_get("/hud-state").await
}

#[tauri::command]
async fn submit_task(objective: String) -> Result<Value, String> {
    let url = format!("{BRIDGE_BASE}/tasks");
    let body = serde_json::json!({ "objective": objective });
    let resp = client()?
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("bridge unavailable: {e}"))?;
    let status = resp.status();
    let value = resp
        .json::<Value>()
        .await
        .map_err(|e| format!("bridge decode error: {e}"))?;
    if !status.is_success() {
        let msg = value
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown error");
        return Err(format!("submit_task failed: HTTP {status} - {msg}"));
    }
    Ok(value)
}

#[tauri::command]
async fn fetch_trace(task_id: String) -> Result<Value, String> {
    bridge_get(&format!("/tasks/{task_id}/trace")).await
}

#[tauri::command]
async fn fetch_memory() -> Result<Value, String> {
    bridge_get("/memory").await
}

#[tauri::command]
async fn bridge_health() -> Result<Value, String> {
    bridge_get("/health").await
}

async fn bridge_post(path: &str, body: Value) -> Result<Value, String> {
    let url = format!("{BRIDGE_BASE}{path}");
    let resp = client()?
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("bridge unavailable: {e}"))?;
    let status = resp.status();
    let value = resp
        .json::<Value>()
        .await
        .map_err(|e| format!("bridge decode error: {e}"))?;
    if !status.is_success() {
        let msg = value
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown error");
        return Err(format!("{path} failed: HTTP {status} - {msg}"));
    }
    Ok(value)
}

#[tauri::command]
async fn propose_action(
    capability: String,
    parameters: Value,
    task_id: Option<String>,
    intent: Option<String>,
    confidence: Option<f64>,
    dry_run: Option<bool>,
) -> Result<Value, String> {
    let mut body = serde_json::json!({
        "capability": capability,
        "parameters": parameters,
    });
    if let Some(t) = task_id { body["task_id"] = Value::String(t); }
    if let Some(i) = intent { body["intent"] = Value::String(i); }
    if let Some(c) = confidence {
        body["confidence"] = serde_json::json!(c);
    }
    if let Some(d) = dry_run { body["dry_run"] = Value::Bool(d); }
    bridge_post("/actions/propose", body).await
}

#[tauri::command]
async fn execute_action(approval_id: String) -> Result<Value, String> {
    bridge_post("/actions/execute", serde_json::json!({ "approval_id": approval_id })).await
}

#[tauri::command]
async fn deny_action(approval_id: String, reason: Option<String>) -> Result<Value, String> {
    let body = serde_json::json!({
        "approval_id": approval_id,
        "reason": reason.unwrap_or_default(),
    });
    bridge_post("/actions/deny", body).await
}

#[tauri::command]
async fn list_approvals() -> Result<Value, String> {
    bridge_get("/approvals").await
}

#[tauri::command]
async fn fetch_action(action_id: String) -> Result<Value, String> {
    bridge_get(&format!("/actions/{action_id}")).await
}

#[tauri::command]
async fn voice_state() -> Result<Value, String> {
    bridge_get("/voice").await
}

#[tauri::command]
async fn voice_start() -> Result<Value, String> {
    bridge_post("/voice/start", serde_json::json!({})).await
}

#[tauri::command]
async fn voice_stop(audio_base64: String, mime: Option<String>) -> Result<Value, String> {
    let body = serde_json::json!({
        "audio_base64": audio_base64,
        "mime": mime.unwrap_or_else(|| "audio/webm".to_string()),
    });
    let url = format!("{BRIDGE_BASE}/voice/stop");
    let resp = voice_client()?
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("bridge unavailable: {e}"))?;
    let status = resp.status();
    let value = resp
        .json::<Value>()
        .await
        .map_err(|e| format!("bridge decode error: {e}"))?;
    if !status.is_success() {
        let msg = value
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown error");
        return Err(format!("/voice/stop failed: HTTP {status} - {msg}"));
    }
    Ok(value)
}

#[tauri::command]
async fn voice_submit(transcript: Option<String>, create_task: Option<bool>) -> Result<Value, String> {
    let mut body = serde_json::json!({
        "create_task": create_task.unwrap_or(true),
    });
    if let Some(t) = transcript {
        body["transcript"] = Value::String(t);
    }
    bridge_post("/voice/submit", body).await
}

#[tauri::command]
async fn voice_discard() -> Result<Value, String> {
    bridge_post("/voice/discard", serde_json::json!({})).await
}

#[tauri::command]
async fn voice_reset() -> Result<Value, String> {
    bridge_post("/voice/reset", serde_json::json!({})).await
}

#[tauri::command]
async fn voice_enable(enabled: bool) -> Result<Value, String> {
    bridge_post("/voice/enable", serde_json::json!({ "enabled": enabled })).await
}

#[tauri::command]
async fn browser_context() -> Result<Value, String> {
    bridge_get("/browser/context").await
}

#[tauri::command]
async fn browser_snapshot(url: String, title: Option<String>, text: Option<String>) -> Result<Value, String> {
    let mut body = serde_json::json!({ "url": url });
    if let Some(t) = title {
        body["title"] = Value::String(t);
    }
    if let Some(t) = text {
        body["text"] = Value::String(t);
    }
    bridge_post("/browser/snapshot", body).await
}

#[tauri::command]
async fn browser_clear() -> Result<Value, String> {
    bridge_post("/browser/clear", serde_json::json!({})).await
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            health,
            get_hud_state,
            submit_task,
            fetch_trace,
            fetch_memory,
            bridge_health,
            propose_action,
            execute_action,
            deny_action,
            list_approvals,
            fetch_action,
            voice_state,
            voice_start,
            voice_stop,
            voice_submit,
            voice_discard,
            voice_reset,
            voice_enable,
            browser_context,
            browser_snapshot,
            browser_clear,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Jarvis HUD");
}
