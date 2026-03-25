;; no_action.wat — WASM policy module for no_action
;;
;; no_action is always allowed regardless of FSM state or score.
;; This module exists so every dispatched action has a corresponding
;; sandbox module — no special-casing in the executor.
;;
;; FSM state encoding (must match wasm_executor.py):
;;   Healthy=0  Degraded=1  Audited=2  Repairing=3  Recovered=4
;;
;; Exports:
;;   policy_check(fsm_state: i32, score: f32) -> i32
;;   Returns 1 (allowed) unconditionally.

(module
  (func (export "policy_check") (param $fsm_state i32) (param $score f32) (result i32)
    (i32.const 1)
  )
)
