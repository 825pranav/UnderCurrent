;; checkpoint_restart.wat — WASM policy module for checkpoint_and_restart
;;
;; checkpoint_and_restart is major remediation (CRIU checkpoint + docker restart).
;; It is valid when:
;;   1. FSM state is Audited (2) or Repairing (3)
;;      - Audited:   container was flushed and is under observation; escalating
;;        to checkpoint is the correct next step in the FSM sequence.
;;      - Repairing: a checkpoint was already approved; a retry is permitted
;;        if the action did not fully resolve the issue.
;;   2. score >= 0.80 (REPAIR_THRESHOLD)
;;
;; Blocked in: Healthy, Degraded (flush must happen first — FSM sequence
;; enforced), Recovered (container is stable; no major action needed).
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
    ;; FSM must be Repairing (3) — retry permitted
    (if (i32.eq (local.get $fsm_state) (i32.const 3))
      (then (return (i32.const 1)))
    )
    ;; Degraded, Healthy, Recovered: blocked — sequence not satisfied
    (i32.const 0)
  )
)
