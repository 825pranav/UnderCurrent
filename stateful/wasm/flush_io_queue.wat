;; flush_io_queue.wat — WASM policy module for flush_io_queue
;;
;; flush_io_queue is the first-line remediation action.  It is valid when:
;;   1. FSM state is Degraded (1) or Audited (2)
;;      - Degraded: container just crossed the flush threshold; flush is the
;;        correct first response before escalating to checkpoint.
;;      - Audited:  a previous flush already fired the audit transition; a
;;        repeat flush is permitted while under observation.
;;   2. score >= 0.50 (FLUSH_THRESHOLD)
;;
;; Blocked in: Healthy (no degradation present), Repairing (repair in progress
;; — flushing would interfere), Recovered (use heal path instead).
;;
;; FSM state encoding (must match wasm_executor.py):
;;   Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
;;
;; Exports:
;;   policy_check(fsm_state: i32, score: f32) -> i32
;;   Returns 1 (allowed) or 0 (blocked).

(module
  (func (export "policy_check") (param $fsm_state i32) (param $score f32) (result i32)
    ;; score must be >= 0.50
    (if (f32.lt (local.get $score) (f32.const 0.50))
      (then (return (i32.const 0)))
    )
    ;; FSM must be Degraded (1)
    (if (i32.eq (local.get $fsm_state) (i32.const 1))
      (then (return (i32.const 1)))
    )
    ;; FSM must be Audited (2)
    (if (i32.eq (local.get $fsm_state) (i32.const 2))
      (then (return (i32.const 1)))
    )
    ;; all other states: blocked
    (i32.const 0)
  )
)
