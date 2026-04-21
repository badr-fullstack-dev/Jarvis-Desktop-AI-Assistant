#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;

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

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![health])
        .run(tauri::generate_context!())
        .expect("error while running Jarvis HUD");
}

