;; checkpoint_restart.wat — WASM policy module for checkpoint_and_restart
;;
;; checkpoint_and_restart is major remediation (CRIU checkpoint + docker restart).
;; It is valid when:
;;   1. FSM state is Audited (2) — container has been flushed and observed;
;;      checkpoint is the correct next escalation in the FSM sequence.
;;   2. score >= 0.80 (REPAIR_THRESHOLD)
;;
;; Blocked in: Healthy, Degraded (flush must happen first — FSM sequence
;; enforced), Repairing (reconcile.py Step 3 enforces Audited-only dispatch
;; before the WASM gate; a retry from Repairing is not a supported path),
;; Recovered (container is stable; no major action needed).
;;
;; This is the key FSM-sequence enforcement for the research paper:
;; checkpoint_and_restart cannot be dispatched from Degraded directly —
;; the container MUST have gone through Audited first (flush + observation).
;;
;; FSM state encoding (must match wasm_executor.py):
;;   Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
;;
;; Exports:
;;   policy_check(fsm_state: i32, score: f32) -> i32
;;   Returns 1 (allowed) or 0 (blocked).

(module
  (func (export "policy_check") (param $fsm_state i32) (param $score f32) (result i32)
    ;; score must be >= 0.80
    (if (f32.lt (local.get $score) (f32.const 0.80))
      (then (return (i32.const 0)))
    )
    ;; FSM must be Audited (2)
    (if (i32.eq (local.get $fsm_state) (i32.const 2))
      (then (return (i32.const 1)))
    )
    ;; All other states (Healthy, Degraded, Repairing, Recovered): blocked
    (i32.const 0)
  )
)
