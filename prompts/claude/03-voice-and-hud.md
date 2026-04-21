# Claude Prompt: Voice Pipeline and Live HUD

Implement the voice-first control layer and connect it to the HUD.

Goals:
- Add wake-word-only activation with explicit privacy boundaries.
- Add STT/TTS provider abstractions with hybrid local/cloud routing.
- Replace demo HUD data with live task, approval, trace, and memory streams.

Deliverables:
- Voice session coordinator with wake, listen, transcribe, confirm, and respond states.
- Live approval center in the HUD backed by supervisor events.
- Transcript view, agent graph, and trace panel fed from the real runtime.
- Tests for wake flow, interruption handling, and privacy mode behavior.

Constraints:
- Keep continuous ambient listening out of scope for v1.
- Make provider choice policy-driven and inspectable.
- Surface every automated action in the HUD.

