;; escalate.wat — WASM policy module for escalate
;;
;; escalate pages the on-call operator for manual intervention.
;; It is valid when:
;;   1. FSM state is Degraded (1), Audited (2), or Repairing (3)
;;      - Any active degradation state qualifies: the system has already
;;        detected a fault and automated remediation is in progress or has
;;        been attempted.  Human escalation is appropriate.
;;   2. score >= 0.95 (ESCALATE_THRESHOLD)
;;
;; Blocked in: Healthy (no fault present — spurious escalation),
;; Recovered (fault resolved — escalation unnecessary).
;;
;; FSM state encoding (must match wasm_executor.py):
;;   Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
;;
;; Exports:
;;   policy_check(fsm_state: i32, score: f32) -> i32
;;   Returns 1 (allowed) or 0 (blocked).

(module
  (func (export "policy_check") (param $fsm_state i32) (param $score f32) (result i32)
    ;; score must be >= 0.95
    (if (f32.lt (local.get $score) (f32.const 0.95))
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
    ;; FSM must be Repairing (3)
    (if (i32.eq (local.get $fsm_state) (i32.const 3))
      (then (return (i32.const 1)))
    )
    ;; Healthy or Recovered: blocked
    (i32.const 0)
  )
)
