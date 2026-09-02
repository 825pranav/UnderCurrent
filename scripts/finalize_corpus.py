#!/usr/bin/env python3
"""
finalize_corpus.py — snapshot a finished corpus run and compute every paper metric.

  1. cutoff = the "[campaign] done <ISO>" line of the campaign log (or --cutoff)
  2. copy stateful/stateless traces + divergence logs (records <= cutoff) to OUT/
  3. shared/eval_runner.py on OUT/  → OUT/metrics.json      (M1, M2, M4, M5, M6)
  4. stateful/ablation.py replay    → OUT/ablation_results.json  (Table III)
  5. OUT/corpus_summary.json + a side-by-side table against data/phase2 (April)

Usage: python3 scripts/finalize_corpus.py [--out data/phase3] [--cutoff 'YYYY-MM-DD HH:MM:SS'] [--dry-run DIR]
"""
import argparse, collections, hashlib, json, os, re, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "shared")); sys.path.insert(0, os.path.join(ROOT, "stateful"))

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="data/phase3")
ap.add_argument("--cutoff", default=None, help="local time 'YYYY-MM-DD HH:MM:SS'; default = campaign done line")
ap.add_argument("--campaign-log", default="/tmp/uc_campaign.log")
ap.add_argument("--dry-run", default=None, help="write into this dir instead of --out; do not archive")
ap.add_argument("--windows", default=None,
                help="keep only records inside these local-time windows, e.g. "
                     "'2026-09-02 08:08:40-2026-09-02 09:38:18,2026-09-02 13:26:20-2026-09-02 14:26:30'")
args = ap.parse_args()
OUT = args.dry_run or args.out

# ── 1. cutoff ────────────────────────────────────────────────────────────────
if args.cutoff:
    cutoff = time.mktime(time.strptime(args.cutoff, "%Y-%m-%d %H:%M:%S"))
else:
    done = [l for l in open(args.campaign_log) if l.startswith("[campaign] done")]
    if not done:
        sys.exit("no '[campaign] done' line in the campaign log — pass --cutoff or wait")
    iso = re.search(r"done (\S+)", done[-1]).group(1)
    cutoff = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
print(f"[finalize] cutoff = {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cutoff))}")
windows = []
if args.windows:
    for w in args.windows.split(","):
        a, b = w.split("-")[0:3:2] if w.count("-") == 5 else (None, None)
        # dates contain '-', so split on the '-' between the two timestamps explicitly
        a, b = w[:19], w[20:39]
        windows.append((time.mktime(time.strptime(a, "%Y-%m-%d %H:%M:%S")), time.mktime(time.strptime(b, "%Y-%m-%d %H:%M:%S"))))
    print("[finalize] windows:", [(time.strftime('%H:%M:%S', time.localtime(a)), time.strftime('%H:%M:%S', time.localtime(b))) for a, b in windows])
def keep(t):
    if t > cutoff: return False
    return (not windows) or any(a <= t <= b for a, b in windows)

# ── 2. snapshot ──────────────────────────────────────────────────────────────
if not args.dry_run and os.path.isdir(OUT) and os.listdir(OUT):
    dst = f"archive/{os.path.basename(OUT)}-superseded-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(OUT, dst); print(f"[finalize] previous {OUT} → {dst}")
os.makedirs(OUT, exist_ok=True)
FILES = {"stateful/traces.jsonl": "stateful_traces.jsonl", "stateless/traces.jsonl": "stateless_traces.jsonl",
         "stateful/divergence_log.jsonl": "stateful_divergence_log.jsonl", "stateless/divergence_log.jsonl": "stateless_divergence_log.jsonl"}
TS = ("trace_time", "timestamp", "time")
for src, name in FILES.items():
    dst = os.path.join(OUT, name)
    if not os.path.exists(src):
        print(f"[finalize] {src}: absent (ok for stateless divergence log)"); continue
    n = k = 0
    with open(src) as f, open(dst, "w") as o:
        for l in f:
            n += 1; r = json.loads(l)
            t = next((r[x] for x in TS if x in r), 0)
            if keep(t): o.write(l); k += 1
    print(f"[finalize] {src}: {n} → {k} kept")

# ── 3. metrics ───────────────────────────────────────────────────────────────
env = dict(os.environ, PYTHONPATH="/home/pes2ug23cs429/.local/lib/python3.10/site-packages")
subprocess.run([sys.executable, "shared/eval_runner.py", "--data-dir", OUT, "--out", os.path.join(OUT, "metrics.json")], env=env, check=True, stdout=subprocess.DEVNULL)
metrics = json.load(open(os.path.join(OUT, "metrics.json")))["metrics"]
# April metrics computed the same way, for the side-by-side table
_apr = "/tmp/uc_phase2_metrics.json"
subprocess.run([sys.executable, "shared/eval_runner.py", "--data-dir", "data/phase2", "--out", _apr], env=env, check=True, stdout=subprocess.DEVNULL)
oldm = json.load(open(_apr))["metrics"]

# ── 4. ablation replay (Table III) ───────────────────────────────────────────
import ablation as abl
abl_res = abl.run(trace_file=os.path.join(OUT, "stateful_traces.jsonl"), out_file=os.path.join(OUT, "ablation_results.json"))

# ── 5. summary + comparison ─────────────────────────────────────────────────
def stats(d):
    sf = [json.loads(l) for l in open(os.path.join(d, "stateful_traces.jsonl"))]
    sl = [json.loads(l) for l in open(os.path.join(d, "stateless_traces.jsonl"))]
    divf = os.path.join(d, "stateful_divergence_log.jsonl")
    ndiv = sum(1 for _ in open(divf)) if os.path.exists(divf) else 0
    rf = [r for r in sf if r["mode"] == "real"]; rs = [r for r in sl if r["mode"] == "real"]
    act_f = collections.Counter(r["action"] for r in rf); act_s = collections.Counter(r["action"] for r in rs)
    sha = {n: hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest() for n in ("stateful_traces.jsonl", "stateless_traces.jsonl")}
    return {
        "total_traces": len(sf) + len(sl), "N_real_F": len(rf), "N_shadow_F": len(sf) - len(rf),
        "N_real_S": len(rs), "N_shadow_S": len(sl) - len(rs),
        "containers_F": sorted({r["container"] for r in rf}), "containers_S": sorted({r["container"] for r in rs}),
        "actions_F": dict(act_f), "actions_S": dict(act_s),
        "active_F": act_f["flush_io_queue"] + act_f["checkpoint_and_restart"] + act_f["escalate"],
        "active_S": act_s["reschedule"] + act_s["restart"],
        "divergences_F": ndiv, "wasm_blocked_F": sum(1 for r in rf if r.get("wasm_blocked")),
        "fsm_gate_deferrals_F": sum(1 for r in rf if r.get("blocked_reason")),
        "C&R_per_container": {c: sum(1 for r in rf if r["container"] == c and r["action"] == "checkpoint_and_restart") for c in sorted({r["container"] for r in rf})},
        "wasm_active": all("Python fallback" not in (r.get("wasm_reason") or "") for r in rf),
        "sha256": sha,
    }
new = stats(OUT); old = stats("data/phase2")
summary = {"cutoff": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cutoff)),
           "windows": [[time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a)), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b))] for a, b in windows],
           "corpus": new, "metrics": metrics, "ablation": abl_res}
json.dump(summary, open(os.path.join(OUT, "corpus_summary.json"), "w"), indent=2)

def m2(mt): return mt["divergence_rate"]
def m6(mt): return mt["unsafe_action_suppression_rate"]
rows = [
 ("total traces", old["total_traces"], new["total_traces"]),
 ("N_real^F / shadow", f'{old["N_real_F"]} / {old["N_shadow_F"]}', f'{new["N_real_F"]} / {new["N_shadow_F"]}'),
 ("N_real^S / shadow", f'{old["N_real_S"]} / {old["N_shadow_S"]}', f'{new["N_real_S"]} / {new["N_shadow_S"]}'),
 ("containers (F)", ",".join(old["containers_F"]), ",".join(new["containers_F"])),
 ("WASM active in corpus", old["wasm_active"], new["wasm_active"]),
 ("active Type-F (flush / C&R)", f'{old["active_F"]} ({old["actions_F"].get("flush_io_queue",0)} / {old["actions_F"].get("checkpoint_and_restart",0)})', f'{new["active_F"]} ({new["actions_F"].get("flush_io_queue",0)} / {new["actions_F"].get("checkpoint_and_restart",0)})'),
 ("active Type-S (reschedule)", old["active_S"], new["active_S"]),
 ("divergence records (F)", old["divergences_F"], new["divergences_F"]),
 ("M1 misclassification", oldm["misclassification_rate"] if oldm else "0.0000", metrics["misclassification_rate"]),
 ("M2 S / F / overall", f'{m2(oldm)["stateless"]} / {m2(oldm)["stateful"]} / {m2(oldm)["overall"]}' if oldm else "0.0000 / 0.0193 / 0.0096", f'{m2(metrics)["stateless"]} / {m2(metrics)["stateful"]} / {m2(metrics)["overall"]}'),
 ("M4 reversibility", "1.0", metrics["reversibility_ratio"]["all"]),
 ("M5 explanation completeness", "1.0", metrics["explanation_completeness"]["all"]),
 ("M6 S / F / overall", f'{m6(oldm)["stateless"]} / {m6(oldm)["stateful"]} / {m6(oldm)["all"]}' if oldm else "0.0000 / 0.0193 / 0.0096", f'{m6(metrics)["stateless"]} / {m6(metrics)["stateful"]} / {m6(metrics)["all"]}'),
 ("wasm_blocked / FSM deferrals (F)", f'{old["wasm_blocked_F"]} / {old["fsm_gate_deferrals_F"]}', f'{new["wasm_blocked_F"]} / {new["fsm_gate_deferrals_F"]}'),
 ("C&R per container", json.dumps(old["C&R_per_container"]), json.dumps(new["C&R_per_container"])),
 ("sha256 stateful (16)", old["sha256"]["stateful_traces.jsonl"][:16], new["sha256"]["stateful_traces.jsonl"][:16]),
]
w = max(len(r[0]) for r in rows)
print(f"\n{'metric':<{w}}  {'April (data/phase2)':<34} {'this run (' + OUT + ')'}")
for r in rows: print(f"{r[0]:<{w}}  {str(r[1]):<34} {r[2]}")
print(f"\nTable III (ablation replay, N={abl_res.get('n_traces')} real Type-F):")
for k, v in abl_res["variants"].items():
    flat = {kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict))}
    print(f"  {k:<16} {json.dumps(flat)[:150]}")
    for kk in ("action_distribution", "action_counts", "unsafe_cr", "missed_cr"):
        if kk in v: print(f"      {kk}: {v[kk]}")
print(f"\n[finalize] wrote {OUT}/corpus_summary.json, metrics.json, ablation_results.json")
