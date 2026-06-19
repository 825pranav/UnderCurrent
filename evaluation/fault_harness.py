# evaluation/fault_harness.py — UnderCurrent Fault Scenario Catalog
#
# Defines 12 fault injection scenarios covering single, compound, intermittent,
# and cascading failure modes across both S (stateless) and F (stateful) tracks.
# Provides a deterministic event generator with optional timing jitter so that
# every trial in run_trials.py is reproducible given the same seed.
#
# Usage (standalone):
#   python evaluation/fault_harness.py
#
# Usage (as library):
#   from evaluation.fault_harness import FAULT_SCENARIOS, generate_events, get_scenario

import sys
import os
import random
import time

# ── sys.path: ensure sibling packages are importable ──────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'stateless'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'stateful'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))


# ── Fault scenario catalog ─────────────────────────────────────────────────────

FAULT_SCENARIOS = [
    # ── Single faults — S track ───────────────────────────────────────────────
    {
        "id":               "cpu_spike_01",
        "type":             "tcp_connect_burst",
        "category":         "single",
        "track":            "S",
        "expected_action":  "restart",
        "expected_sequence": ["restart"],
        "inject_params": {
            "event":        "tcp_connect",
            "count":        5,
            "spread_s":     0.5,
            "jitter_s":     0.2,
            "container":    "sim-s-cpu-spike",
        },
        "description": (
            "Inject 5 tcp_connect events within a short burst. "
            "Score = 5/5 × 0.90 = 0.90 >= RESTART_THRESHOLD (0.80). "
            "Expected action: restart."
        ),
    },
    {
        "id":               "memory_leak_01",
        "type":             "tcp_connect_spread",
        "category":         "single",
        "track":            "S",
        "expected_action":  "restart",
        "expected_sequence": ["restart"],
        "inject_params": {
            "event":        "tcp_connect",
            "count":        5,
            "spread_s":     2.0,
            "jitter_s":     0.3,
            "container":    "sim-s-memleak",
        },
        "description": (
            "Inject 5 tcp_connect events spread over 2 seconds to simulate "
            "a slow-onset memory leak causing repeated connection failures. "
            "Score = 0.90 >= RESTART_THRESHOLD. Expected action: restart."
        ),
    },
    {
        "id":               "network_drop_01",
        "type":             "process_exit_single",
        "category":         "single",
        "track":            "S",
        "expected_action":  "reschedule",
        "expected_sequence": ["reschedule"],
        "inject_params": {
            "event":        "process_exit",
            "count":        1,
            "spread_s":     0.0,
            "jitter_s":     0.1,
            "container":    "sim-s-netdrop",
        },
        "description": (
            "Inject a single process_exit event. "
            "Score = 0.95 >= RESCHEDULE_THRESHOLD (0.95). "
            "Expected action: reschedule."
        ),
    },
    {
        "id":               "disk_full_01",
        "type":             "tcp_connect_excess",
        "category":         "single",
        "track":            "S",
        "expected_action":  "restart",
        "expected_sequence": ["restart"],
        "inject_params": {
            "event":        "tcp_connect",
            "count":        7,
            "spread_s":     1.0,
            "jitter_s":     0.2,
            "container":    "sim-s-diskfull",
        },
        "description": (
            "Inject 7 tcp_connect events (beyond the scale ceiling of 5). "
            "Score = min(7/5, 1.0) × 0.90 = 0.90 (capped). "
            "Expected action: restart."
        ),
    },
    # ── Single faults — F track ───────────────────────────────────────────────
    {
        "id":               "io_degradation_01",
        "type":             "blk_io_latency_high",
        "category":         "single",
        "track":            "F",
        "expected_action":  "flush_io_queue",
        "expected_sequence": ["flush_io_queue", "checkpoint_and_restart"],
        "inject_params": {
            "event":        "blk_io_latency",
            "count":        5,
            "latency_us":   15000,
            "spread_s":     1.0,
            "jitter_s":     0.2,
            "container":    "sim-f-io-degrad",
            "volume_path":  "/data",
        },
        "description": (
            "Inject 5 high-latency blk_io_latency events (latency_us=15000). "
            "Score = 5/5 × 0.85 = 0.85 >= REPAIR_THRESHOLD (0.80). "
            "FSM starts at Healthy; cycle 1: degrade fires → Degraded, "
            "checkpoint_and_restart deferred to flush_io_queue (not yet Audited). "
            "Cycle 2: FSM is Audited → checkpoint_and_restart allowed. "
            "expected_sequence: [flush_io_queue, checkpoint_and_restart]."
        ),
    },
    {
        "id":               "volume_lost_01",
        "type":             "volume_mount_lost",
        "category":         "single",
        "track":            "F",
        "expected_action":  "escalate",
        "expected_sequence": ["escalate"],
        "inject_params": {
            "event":        "volume_mount_lost",
            "count":        1,
            "spread_s":     0.0,
            "jitter_s":     0.1,
            "container":    "sim-f-vollost",
            "volume_path":  "/data",
            "latency_us":   0,
        },
        "description": (
            "Inject a single volume_mount_lost event. "
            "Score = 0.97 >= ESCALATE_THRESHOLD (0.95). "
            "FSM fires degrade (Healthy → Degraded); escalate is allowed from Degraded. "
            "Expected action: escalate."
        ),
    },
    {
        "id":               "vfs_errors_01",
        "type":             "vfs_write_error_burst",
        "category":         "single",
        "track":            "F",
        "expected_action":  "checkpoint_and_restart",
        "expected_sequence": ["flush_io_queue", "checkpoint_and_restart"],
        "inject_params": {
            "event":        "vfs_write_error",
            "count":        3,
            "spread_s":     0.5,
            "jitter_s":     0.2,
            "container":    "sim-f-vfserr",
            "volume_path":  "/var/lib/data",
            "latency_us":   0,
        },
        "description": (
            "Inject 3 vfs_write_error events. "
            "Score = 3/3 × 0.88 = 0.88 >= REPAIR_THRESHOLD (0.80). "
            "FSM starts Healthy; cycle 1: checkpoint_and_restart deferred → flush_io_queue. "
            "Cycle 2: FSM is Audited → checkpoint_and_restart dispatched. "
            "expected_sequence: [flush_io_queue, checkpoint_and_restart]."
        ),
    },
    # ── Compound faults ────────────────────────────────────────────────────────
    {
        "id":               "compound_cpu_io_01",
        "type":             "process_exit_plus_tcp_fail",
        "category":         "compound",
        "track":            "S",
        "expected_action":  "reschedule",
        "expected_sequence": ["reschedule"],
        "inject_params": {
            "events": [
                {"event": "process_exit",    "count": 1, "spread_s": 0.0},
                {"event": "tcp_connect", "count": 3, "spread_s": 0.5},
            ],
            "jitter_s":  0.2,
            "container": "sim-s-compound-cpu-io",
        },
        "description": (
            "Inject 1 process_exit + 3 tcp_connect events. "
            "Stateless scorer takes max: process_exit → 0.95, tcp_connect 3/5 → 0.54. "
            "Max = 0.95 >= RESCHEDULE_THRESHOLD. process_exit dominates. "
            "Expected action: reschedule."
        ),
    },
    {
        "id":               "compound_network_memory_01",
        "type":             "process_exit_plus_tcp_fail_heavy",
        "category":         "compound",
        "track":            "S",
        "expected_action":  "reschedule",
        "expected_sequence": ["reschedule"],
        "inject_params": {
            "events": [
                {"event": "process_exit",    "count": 1, "spread_s": 0.0},
                {"event": "tcp_connect", "count": 5, "spread_s": 1.0},
            ],
            "jitter_s":  0.2,
            "container": "sim-s-compound-net-mem",
        },
        "description": (
            "Inject 1 process_exit + 5 tcp_connect events. "
            "Scores: process_exit → 0.95, tcp_connect → 0.90. "
            "Max = 0.95 >= RESCHEDULE_THRESHOLD. "
            "Expected action: reschedule."
        ),
    },
    # ── Intermittent / gray-zone ───────────────────────────────────────────────
    {
        "id":               "cpu_flicker_01",
        "type":             "tcp_connect_low",
        "category":         "intermittent",
        "track":            "S",
        "expected_action":  "no_action",
        "expected_sequence": ["no_action"],
        "inject_params": {
            "event":        "tcp_connect",
            "count":        2,
            "spread_s":     5.0,
            "jitter_s":     0.5,
            "container":    "sim-s-flicker",
        },
        "description": (
            "Inject 2 tcp_connect events spread over 5 seconds. "
            "Score = 2/5 × 0.90 = 0.36, well below RESTART_THRESHOLD (0.80). "
            "Tests conservative no-action behavior for transient, low-frequency signals."
        ),
    },
    {
        "id":               "ambiguous_01",
        "type":             "tcp_connect_borderline",
        "category":         "intermittent",
        "track":            "S",
        "expected_action":  "no_action",
        "expected_sequence": ["no_action"],
        "inject_params": {
            "event":        "tcp_connect",
            "count":        4,
            "spread_s":     1.0,
            "jitter_s":     0.3,
            "container":    "sim-s-ambiguous",
        },
        "description": (
            "Inject 4 tcp_connect events. "
            "Score = 4/5 × 0.90 = 0.72 < RESTART_THRESHOLD (0.80). "
            "Gray-zone scenario: one event short of triggering restart. "
            "Expected action: no_action (just below threshold — conservative)."
        ),
    },
    # ── Cascading faults ──────────────────────────────────────────────────────
    {
        "id":               "cascade_io_volume_01",
        "type":             "blk_io_then_volume_lost",
        "category":         "cascading",
        "track":            "F",
        "expected_action":  "escalate",
        "expected_sequence": ["flush_io_queue", "escalate"],
        "inject_params": {
            "phases": [
                {
                    "event":       "blk_io_latency",
                    "count":       5,
                    "latency_us":  15000,
                    "spread_s":    1.0,
                    "container":   "sim-f-cascade",
                    "volume_path": "/data",
                },
                {
                    "event":       "volume_mount_lost",
                    "count":       1,
                    "latency_us":  0,
                    "spread_s":    0.0,
                    "container":   "sim-f-cascade",
                    "volume_path": "/data",
                },
            ],
            "jitter_s":       0.2,
            "phase_gap_s":    2.0,
        },
        "description": (
            "Two-phase cascading failure. "
            "Phase 1: 5 high-latency blk_io_latency events → score 0.85 → flush_io_queue. "
            "Phase 2: volume_mount_lost event → score 0.97 → escalate. "
            "FSM state advances: Healthy → Degraded → Audited (phase 1), then escalate (phase 2). "
            "expected_sequence: [flush_io_queue, escalate]."
        ),
    },
]


# ── Index helpers ──────────────────────────────────────────────────────────────

_SCENARIO_INDEX = {s["id"]: s for s in FAULT_SCENARIOS}


def get_scenario(scenario_id: str) -> dict:
    """
    Retrieve a single scenario dict by its string identifier.

    Args:
        scenario_id: the 'id' field of the desired scenario (e.g. 'cpu_spike_01').

    Returns:
        The scenario dict, or raises KeyError if not found.
    """
    if scenario_id not in _SCENARIO_INDEX:
        raise KeyError(f"Unknown scenario id: {scenario_id!r}. "
                       f"Available: {sorted(_SCENARIO_INDEX)}")
    return _SCENARIO_INDEX[scenario_id]


def list_scenarios(category: str = None, track: str = None) -> list:
    """
    Return a filtered list of scenario dicts.

    Args:
        category: optional filter — one of 'single', 'compound',
                  'intermittent', 'cascading'.
        track:    optional filter — 'S', 'F', or 'both'.

    Returns:
        List of matching scenario dicts (preserves catalog order).
    """
    result = list(FAULT_SCENARIOS)
    if category is not None:
        result = [s for s in result if s["category"] == category]
    if track is not None:
        result = [s for s in result if s["track"] in (track, "both")]
    return result


# ── Event template helpers ────────────────────────────────────────────────────

def _base_event(container: str, event_type: str, pid: int,
                ts: float, track: str, extra: dict = None) -> dict:
    """
    Build a minimal event dict compatible with both StateStore and StatefulStateStore.

    Args:
        container:  container name string.
        event_type: eBPF event name (e.g. 'tcp_connect').
        pid:        synthetic process ID for this event.
        ts:         unix timestamp float.
        track:      'S' or 'F' — determines node_type field.
        extra:      optional extra fields merged into the event (e.g. latency_us).

    Returns:
        Event dict ready to pass to store.record().
    """
    ev = {
        "container": container,
        "pid":       pid,
        "event":     event_type,
        "time":      ts,
        "node_type": track,
    }
    if extra:
        ev.update(extra)
    return ev


def _jitter_ts(base_ts: float, jitter_s: float, rng: random.Random) -> float:
    """
    Apply a uniform random jitter in [-jitter_s, +jitter_s] to a timestamp.

    Args:
        base_ts:  base unix timestamp.
        jitter_s: maximum absolute jitter magnitude in seconds.
        rng:      seeded Random instance for reproducibility.

    Returns:
        Jittered timestamp float.
    """
    if jitter_s <= 0.0:
        return base_ts
    return base_ts + rng.uniform(-jitter_s, jitter_s)


def _generate_simple_events(params: dict, base_ts: float, track: str,
                             jitter: bool, rng: random.Random) -> list:
    """
    Generate events for a simple (non-compound, non-cascading) scenario.

    Distributes `count` events of a single type evenly over `spread_s` seconds,
    applying optional jitter to each timestamp.

    Args:
        params:   inject_params dict from a scenario.
        base_ts:  unix timestamp for the first event.
        track:    'S' or 'F'.
        jitter:   whether to apply timing jitter.
        rng:      seeded Random instance.

    Returns:
        List of event dicts.
    """
    event_type  = params["event"]
    count       = params["count"]
    spread_s    = params.get("spread_s", 0.0)
    jitter_s    = params.get("jitter_s", 0.5) if jitter else 0.0
    container   = params["container"]
    latency_us  = params.get("latency_us", 0)
    volume_path = params.get("volume_path", "/data")

    events = []
    for i in range(count):
        step   = (spread_s / max(count - 1, 1)) * i if count > 1 else 0.0
        ts     = base_ts + step
        ts     = _jitter_ts(ts, jitter_s, rng) if jitter else ts
        pid    = 10000 + i

        extra = {}
        if event_type == "blk_io_latency":
            extra["latency_us"]  = latency_us
            extra["volume_path"] = volume_path
        elif event_type in ("volume_mount_lost", "volume_mount_restored"):
            extra["volume_path"] = volume_path
        elif event_type in ("vfs_write_error", "vfs_read_error"):
            extra["volume_path"] = volume_path

        events.append(_base_event(container, event_type, pid, ts, track, extra))
    return events


def _generate_compound_events(params: dict, base_ts: float, track: str,
                               jitter: bool, rng: random.Random) -> list:
    """
    Generate events for a compound scenario (multiple event types mixed together).

    Each sub-spec in params['events'] is expanded independently; timestamps
    are interleaved in natural order.

    Args:
        params:   inject_params dict with an 'events' list of sub-specs.
        base_ts:  unix timestamp anchor for the compound burst.
        track:    'S' or 'F'.
        jitter:   whether to apply timing jitter.
        rng:      seeded Random instance.

    Returns:
        List of event dicts, sorted by time.
    """
    container = params["container"]
    jitter_s  = params.get("jitter_s", 0.5) if jitter else 0.0
    all_events = []

    pid_base = 20000
    for sub in params["events"]:
        event_type = sub["event"]
        count      = sub["count"]
        spread_s   = sub.get("spread_s", 0.0)
        for i in range(count):
            step = (spread_s / max(count - 1, 1)) * i if count > 1 else 0.0
            ts   = base_ts + step
            ts   = _jitter_ts(ts, jitter_s, rng) if jitter else ts
            ev   = _base_event(container, event_type, pid_base, ts, track)
            all_events.append(ev)
            pid_base += 1

    all_events.sort(key=lambda e: e["time"])
    return all_events


def _generate_cascading_events(params: dict, base_ts: float, track: str,
                                jitter: bool, rng: random.Random) -> list:
    """
    Generate a list of event phases for a cascading scenario.

    Each phase is returned as a separate list so that run_trials.py can feed
    them into the controller in separate reconcile cycles.

    Args:
        params:   inject_params dict with a 'phases' list and 'phase_gap_s'.
        base_ts:  unix timestamp anchor for the first phase.
        track:    'S' or 'F'.
        jitter:   whether to apply timing jitter.
        rng:      seeded Random instance.

    Returns:
        List of phases, where each phase is a list of event dicts.
    """
    jitter_s   = params.get("jitter_s", 0.5) if jitter else 0.0
    phase_gap  = params.get("phase_gap_s", 2.0)
    phases     = []
    phase_ts   = base_ts

    for phase_spec in params["phases"]:
        event_type  = phase_spec["event"]
        count       = phase_spec["count"]
        spread_s    = phase_spec.get("spread_s", 0.0)
        container   = phase_spec["container"]
        latency_us  = phase_spec.get("latency_us", 0)
        volume_path = phase_spec.get("volume_path", "/data")

        phase_events = []
        pid_base = 30000 + len(phases) * 100
        for i in range(count):
            step = (spread_s / max(count - 1, 1)) * i if count > 1 else 0.0
            ts   = phase_ts + step
            ts   = _jitter_ts(ts, jitter_s, rng) if jitter else ts

            extra = {}
            if event_type == "blk_io_latency":
                extra["latency_us"]  = latency_us
                extra["volume_path"] = volume_path
            elif event_type in ("volume_mount_lost", "volume_mount_restored"):
                extra["volume_path"] = volume_path
            elif event_type in ("vfs_write_error", "vfs_read_error"):
                extra["volume_path"] = volume_path

            phase_events.append(
                _base_event(container, event_type, pid_base + i, ts, track, extra)
            )

        phases.append(phase_events)
        phase_ts += spread_s + phase_gap

    return phases


# ── Public event generator ─────────────────────────────────────────────────────

def generate_events(scenario: dict, seed: int = None,
                    jitter: bool = True, base_time: float = None) -> object:
    """
    Generate synthetic fault events for a scenario, ready to feed into a StateStore.

    For non-cascading scenarios, returns a flat list of event dicts.
    For cascading scenarios (category == 'cascading'), returns a list of phases,
    where each phase is itself a list of event dicts. Callers must handle both
    shapes; the category field in the scenario dict declares which shape to expect.

    Reproducibility: pass an integer seed to fix all random choices. The same
    seed+scenario combination will always produce identical event timestamps.

    Args:
        scenario:  a scenario dict from FAULT_SCENARIOS (or from get_scenario()).
        seed:      integer seed for the internal Random instance; None = random.
        jitter:    if True, add timing jitter using inject_params['jitter_s'].
        base_time: unix timestamp anchor for the first event; defaults to now.

    Returns:
        list[dict]  for single/compound/intermittent scenarios, or
        list[list[dict]] for cascading scenarios.
    """
    rng      = random.Random(seed)
    base_ts  = base_time if base_time is not None else time.time()
    params   = scenario["inject_params"]
    track    = scenario["track"]
    category = scenario["category"]

    if category == "cascading":
        return _generate_cascading_events(params, base_ts, track, jitter, rng)

    if category == "compound":
        return _generate_compound_events(params, base_ts, track, jitter, rng)

    # single / intermittent
    return _generate_simple_events(params, base_ts, track, jitter, rng)


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pprint

    print(f"Loaded {len(FAULT_SCENARIOS)} fault scenarios.\n")

    errors = []
    for sc in FAULT_SCENARIOS:
        sid = sc["id"]
        try:
            events = generate_events(sc, seed=42, jitter=True,
                                     base_time=1_700_000_000.0)
            if sc["category"] == "cascading":
                assert isinstance(events, list) and isinstance(events[0], list), \
                    f"{sid}: cascading must return list-of-phases"
                flat = [e for phase in events for e in phase]
                assert len(flat) > 0, f"{sid}: no events generated"
                # All events must have required fields
                for e in flat:
                    for field in ("container", "pid", "event", "time", "node_type"):
                        assert field in e, f"{sid}: missing field {field!r} in {e}"
                n_events = len(flat)
                n_phases = len(events)
                print(f"  [cascading] {sid:<30} {n_phases} phases, {n_events} total events — OK")
            else:
                assert isinstance(events, list), f"{sid}: must return list"
                assert len(events) > 0, f"{sid}: no events generated"
                for e in events:
                    for field in ("container", "pid", "event", "time", "node_type"):
                        assert field in e, f"{sid}: missing field {field!r} in {e}"
                print(f"  [{sc['category']:<11}] {sid:<30} {len(events)} events — OK")

        except Exception as exc:
            errors.append((sid, exc))
            print(f"  ERROR {sid}: {exc}")

    print(f"\nlist_scenarios(category='single') → "
          f"{[s['id'] for s in list_scenarios(category='single')]}")
    print(f"list_scenarios(track='F') → "
          f"{[s['id'] for s in list_scenarios(track='F')]}")
    print(f"get_scenario('volume_lost_01')['expected_action'] → "
          f"{get_scenario('volume_lost_01')['expected_action']}")

    # Reproducibility check
    ev1 = generate_events(get_scenario("cpu_spike_01"), seed=99, jitter=True,
                          base_time=1_700_000_000.0)
    ev2 = generate_events(get_scenario("cpu_spike_01"), seed=99, jitter=True,
                          base_time=1_700_000_000.0)
    assert [e["time"] for e in ev1] == [e["time"] for e in ev2], \
        "Reproducibility failed: same seed yields different timestamps"
    print("\nReproducibility check passed.")

    if errors:
        print(f"\n{len(errors)} scenario(s) FAILED:")
        for sid, exc in errors:
            print(f"  {sid}: {exc}")
        sys.exit(1)
    else:
        print(f"\nAll {len(FAULT_SCENARIOS)} scenarios passed self-test.")
