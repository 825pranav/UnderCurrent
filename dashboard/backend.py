"""
UnderCurrent Dashboard Backend
Flask API server — reads stateless/traces.jsonl and stateful/traces.jsonl
Run: python backend.py   (port 5050)
"""

import json
import os
from collections import defaultdict
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATELESS_TRACES = os.path.join(BASE_DIR, "stateless", "traces.jsonl")
STATEFUL_TRACES  = os.path.join(BASE_DIR, "stateful",  "traces.jsonl")


def _load_file(path: str, track: str) -> list[dict]:
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["track"] = track
                entries.append(obj)
            except json.JSONDecodeError:
                pass
    return entries


def _all_traces() -> list[dict]:
    s = _load_file(STATELESS_TRACES, "S")
    f = _load_file(STATEFUL_TRACES,  "F")
    combined = s + f
    combined.sort(key=lambda x: x.get("trace_time", 0))
    return combined


@app.route("/api/traces")
def api_traces():
    return jsonify(_all_traces())


@app.route("/api/stats")
def api_stats():
    traces = _all_traces()

    total        = len(traces)
    restarts     = sum(1 for t in traces if t.get("action") == "restart")
    reschedules  = sum(1 for t in traces if t.get("action") == "reschedule")
    no_actions   = sum(1 for t in traces if t.get("action") == "no_action")
    flushes      = sum(1 for t in traces if t.get("action") == "flush_io_queue")
    checkpoints  = sum(1 for t in traces if t.get("action") == "checkpoint_and_restart")
    escalations  = sum(1 for t in traces if t.get("action") == "escalate")

    scores       = [t.get("score", 0) for t in traces]
    peak_score   = max(scores) if scores else 0.0

    real_count      = sum(1 for t in traces if t.get("mode") == "real")
    shadow_count    = sum(1 for t in traces if t.get("mode") == "shadow")

    # divergence: real and shadow disagree on action for same container+time_bucket
    real_actions   = {}
    shadow_actions = {}
    for t in traces:
        key = (t.get("container"), round(t.get("trace_time", 0)))
        if t.get("mode") == "real":
            real_actions[key] = t.get("action")
        elif t.get("mode") == "shadow":
            shadow_actions[key] = t.get("action")

    divergence_count = sum(
        1 for k, act in real_actions.items()
        if k in shadow_actions and shadow_actions[k] != act
    )

    return jsonify({
        "total":            total,
        "restarts":         restarts,
        "reschedules":      reschedules,
        "no_actions":       no_actions,
        "flushes":          flushes,
        "checkpoints":      checkpoints,
        "escalations":      escalations,
        "peak_score":       round(peak_score, 4),
        "real_count":       real_count,
        "shadow_count":     shadow_count,
        "divergence_count": divergence_count,
    })


@app.route("/api/containers")
def api_containers():
    traces = _all_traces()
    latest: dict[str, dict] = {}
    for t in traces:
        key = t.get("container", "unknown")
        latest[key] = t

    result = []
    for container, t in latest.items():
        result.append({
            "container": container,
            "track":     t.get("track", "S"),
            "score":     t.get("score", 0),
            "action":    t.get("action", "no_action"),
            "mode":      t.get("mode", "real"),
            "fsm_state": t.get("fsm_state", None),
        })
    return jsonify(result)


@app.route("/api/timeline")
def api_timeline():
    traces = _all_traces()
    # Only real-mode entries for the timeline
    real_traces = [t for t in traces if t.get("mode") == "real"]

    by_container: dict[str, list] = defaultdict(list)
    for t in real_traces:
        by_container[t.get("container", "unknown")].append({
            "time":   t.get("trace_time", 0),
            "score":  t.get("score", 0),
            "action": t.get("action", "no_action"),
            "mode":   t.get("mode", "real"),
            "track":  t.get("track", "S"),
        })

    return jsonify(dict(by_container))


if __name__ == "__main__":
    app.run(port=5050, debug=False)
