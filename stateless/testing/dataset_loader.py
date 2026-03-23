# dataset_loader.py — maps external fault datasets to UnderCurrent's event schema
#
# Supported datasets:
#   - RCAEval   (github.com/phamquiluan/RCAEval)
#   - Alibaba Cluster Trace 2022 (github.com/alibaba/clusterdata)
#   - Synthetic (built-in, no download needed)
#
# All loaders return a list of events in UnderCurrent format:
#   {"container": str, "pid": int, "event": str, "time": float}
# Plus a ground_truth list:
#   {"container": str, "fault_type": str, "inject_time": float}

import csv
import json
import os
import random
import time

# ── RCAEval fault type → UnderCurrent event mapping ──────────────────────────
RCAEVAL_MAP = {
    # Container/pod kills → process_exit
    "ContainerKill":  "process_exit",
    "PodKill":        "process_exit",
    "PodFailure":     "process_exit",

    # Network faults → tcp_connect_fail
    "Delay":          "tcp_connect_fail",
    "Loss":           "tcp_connect_fail",
    "Duplicate":      "tcp_connect_fail",
    "Corrupt":        "tcp_connect_fail",
    "Bandwidth":      "tcp_connect_fail",
    "Partition":      "tcp_connect_fail",
    "ReqAbort":       "tcp_connect_fail",
    "RespAbort":      "tcp_connect_fail",
    "Socket":         "tcp_connect_fail",

    # Resource stress → tcp_connect_fail (resource exhaustion causes conn failures)
    "CPUStress":      "tcp_connect_fail",
    "MemoryStress":   "tcp_connect_fail",
}

# ── Alibaba fault type → UnderCurrent event mapping ──────────────────────────
ALIBABA_MAP = {
    "container_die":      "process_exit",
    "oom_kill":           "process_exit",
    "network_exception":  "tcp_connect_fail",
    "tcp_fail":           "tcp_connect_fail",
    "container_killed":   "process_exit",
}


def load_rcaeval(data_dir: str) -> tuple:
    """
    Load RCAEval dataset from a local directory.

    Expected structure:
        data_dir/
          inject_time.txt       ← Unix timestamp of fault injection
          fault_type.txt        ← fault type string (e.g. "ContainerKill")
          service_name.txt      ← which service was targeted
          metrics/              ← CSV metric files (optional, not used here)

    Returns:
        events       — list of UnderCurrent event dicts
        ground_truth — list of ground truth dicts
    """
    events = []
    ground_truth = []

    # Walk all case subdirectories
    for case_dir in sorted(os.listdir(data_dir)):
        case_path = os.path.join(data_dir, case_dir)
        if not os.path.isdir(case_path):
            continue

        inject_time_file = os.path.join(case_path, "inject_time.txt")
        fault_type_file  = os.path.join(case_path, "fault_type.txt")
        service_file     = os.path.join(case_path, "service_name.txt")

        if not all(os.path.exists(f) for f in [inject_time_file, fault_type_file, service_file]):
            continue

        inject_time  = float(open(inject_time_file).read().strip())
        fault_type   = open(fault_type_file).read().strip()
        service_name = open(service_file).read().strip()

        uc_event = RCAEVAL_MAP.get(fault_type)
        if uc_event is None:
            print(f"  [loader] skipping unmapped fault type: {fault_type}")
            continue

        # Record ground truth
        ground_truth.append({
            "container":  service_name,
            "fault_type": fault_type,
            "uc_event":   uc_event,
            "inject_time": inject_time,
            "case":       case_dir,
        })

        # Generate event stream:
        # For process_exit: one event at inject_time
        # For tcp_connect_fail: burst of events over 30s window (simulates failure rate)
        if uc_event == "process_exit":
            events.append({
                "container": service_name,
                "pid":       random.randint(1000, 99999),
                "event":     "process_exit",
                "time":      inject_time,
            })
        else:
            # Simulate 5–8 tcp failures spread over 30 seconds
            count = random.randint(5, 8)
            for i in range(count):
                events.append({
                    "container": service_name,
                    "pid":       random.randint(1000, 99999),
                    "event":     "tcp_connect_fail",
                    "time":      inject_time + (i * (30.0 / count)),
                })

    events.sort(key=lambda e: e["time"])
    print(f"[loader] RCAEval: loaded {len(ground_truth)} fault cases → {len(events)} events")
    return events, ground_truth


def load_alibaba(csv_path: str) -> tuple:
    """
    Load Alibaba Cluster Trace CSV.

    Expected columns (map to whatever the actual file has):
        container_id, event_type, timestamp, pid (optional)

    Returns:
        events, ground_truth
    """
    events = []
    ground_truth = []

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_type = row.get("event_type", "").strip()
            uc_event = ALIBABA_MAP.get(raw_type)
            if uc_event is None:
                continue

            container = row.get("container_id", row.get("container", "unknown"))
            ts        = float(row.get("timestamp", row.get("time", 0)))
            pid       = int(row.get("pid", random.randint(1000, 99999)))

            events.append({
                "container": container,
                "pid":       pid,
                "event":     uc_event,
                "time":      ts,
            })
            ground_truth.append({
                "container":   container,
                "fault_type":  raw_type,
                "uc_event":    uc_event,
                "inject_time": ts,
            })

    events.sort(key=lambda e: e["time"])
    print(f"[loader] Alibaba: loaded {len(ground_truth)} fault events")
    return events, ground_truth


def load_synthetic(n_containers: int = 5, n_faults: int = 20,
                   window_seconds: int = 300) -> tuple:
    """
    Built-in synthetic dataset — no download needed.
    Generates a mix of process_exit and tcp_connect_fail faults
    with known ground truth for testing the evaluation pipeline.

    Returns:
        events, ground_truth
    """
    containers = [f"service-{i}" for i in range(n_containers)]
    events = []
    ground_truth = []
    base_time = time.time() - window_seconds

    for i in range(n_faults):
        container  = random.choice(containers)
        fault_type = random.choice(["ContainerKill", "ContainerKill",
                                    "Delay", "Loss", "Socket"])
        uc_event   = RCAEVAL_MAP[fault_type]
        inject_time = base_time + random.uniform(0, window_seconds)

        ground_truth.append({
            "container":   container,
            "fault_type":  fault_type,
            "uc_event":    uc_event,
            "inject_time": inject_time,
            "case":        f"synthetic-{i}",
        })

        if uc_event == "process_exit":
            events.append({
                "container": container,
                "pid":       random.randint(1000, 99999),
                "event":     "process_exit",
                "time":      inject_time,
            })
        else:
            count = random.randint(4, 8)
            for j in range(count):
                events.append({
                    "container": container,
                    "pid":       random.randint(1000, 99999),
                    "event":     "tcp_connect_fail",
                    "time":      inject_time + (j * (30.0 / count)),
                })

    events.sort(key=lambda e: e["time"])
    print(f"[loader] Synthetic: {n_faults} faults → {len(events)} events across {n_containers} containers")
    return events, ground_truth


if __name__ == "__main__":
    # Quick test with synthetic data
    events, gt = load_synthetic(n_containers=3, n_faults=10)
    print(f"Events: {len(events)}")
    print(f"Ground truth: {len(gt)}")
    print(f"Sample event:       {events[0]}")
    print(f"Sample ground truth: {gt[0]}")
