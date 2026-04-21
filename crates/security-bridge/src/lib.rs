use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEnvelope {
    pub event_type: String,
    pub payload: serde_json::Value,
    pub previous_signature: String,
}

impl AuditEnvelope {
    pub fn signature(&self) -> String {
        let material = format!(
            "{}:{}:{}",
            self.event_type, self.payload, self.previous_signature
        );
        let digest = Sha256::digest(material.as_bytes());
        format!("{:x}", digest)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskDecision {
    pub capability: String,
    pub risk_tier: u8,
    pub requires_approval: bool,
    pub blocked: bool,
}

impl RiskDecision {
    pub fn describe(&self) -> String {
        if self.blocked {
            return format!("{} is blocked by default policy.", self.capability);
        }
        if self.requires_approval {
            return format!("{} requires explicit approval.", self.capability);
        }
        format!("{} may proceed automatically.", self.capability)
    }
}

