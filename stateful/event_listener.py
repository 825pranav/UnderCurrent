# event_listener.py — F1: Stateful eBPF Event Capture
#
# Real mode:     Attaches eBPF probes to blk_account_io (block I/O latency),
#                VFS write/read error return paths, and volume mount persistence.
#                Requires: root, bcc system package, Linux 4.9+.
#
# Simulate mode: Emits synthetic stateful events for lab use — no root / BPF needed.
#                Driven by main.py --simulate (default).
#
# Event format (superset of stateless event format — fully backward-compatible):
#   {
#     "container":   str,    # container / process name
#     "pid":         int,
#     "event":       str,    # see EVENT_TYPES below
#     "time":        float,  # unix timestamp
#     "latency_us":  int,    # blk_io_latency only — latency in microseconds
#     "volume_path": str,    # volume_* events only
#     "node_type":   "F",    # always "F" — marks event as stateful-track
#   }
#
# EVENT_TYPES:
#   blk_io_latency        — block I/O completed (latency_us present)
#   vfs_write_error       — VFS write syscall returned an error code
#   vfs_read_error        — VFS read syscall returned an error code
#   volume_mount_lost     — volume mount disappeared (volume_path present)
#   volume_mount_restored — volume mount came back  (volume_path present)

import json
import time

# ── eBPF program (real mode only) ─────────────────────────────────────────────
_BPF_PROGRAM = r"""
#include <uapi/linux/ptrace.h>
#include <linux/blkdev.h>
#include <linux/sched.h>

struct f_event_t {
    u32  pid;
    u32  tgid;
    char comm[TASK_COMM_LEN];
    u32  type;          // 0=blk_io_latency  1=vfs_write_error  2=vfs_read_error
    u64  latency_ns;    // type 0 only
};

BPF_PERF_OUTPUT(f_events);
BPF_HASH(blk_start_ts, u32, u64);   // pid → block I/O start timestamp (ns)

// Record block I/O start time
int trace_blk_start(struct pt_regs *ctx, struct request *req) {
    u32 pid = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    u64 ts  = bpf_ktime_get_ns();
    blk_start_ts.update(&pid, &ts);
    return 0;
}

// On block I/O completion: compute latency and emit event
int trace_blk_done(struct pt_regs *ctx, struct request *req) {
    u32 pid   = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    u64 *start = blk_start_ts.lookup(&pid);
    if (!start) return 0;
    u64 delta_ns = bpf_ktime_get_ns() - *start;
    blk_start_ts.delete(&pid);

    struct f_event_t e = {};
    e.pid        = pid;
    e.tgid       = bpf_get_current_pid_tgid() >> 32;
    e.type       = 0;
    e.latency_ns = delta_ns;
    bpf_get_current_comm(&e.comm, sizeof(e.comm));
    f_events.perf_submit(ctx, &e, sizeof(e));
    return 0;
}

// VFS write errors (kretprobe — fires on return with negative value)
int trace_vfs_write_ret(struct pt_regs *ctx) {
    long ret = PT_REGS_RC(ctx);
    if (ret >= 0) return 0;
    struct f_event_t e = {};
    e.pid  = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    e.tgid = bpf_get_current_pid_tgid() >> 32;
    e.type = 1;
    bpf_get_current_comm(&e.comm, sizeof(e.comm));
    f_events.perf_submit(ctx, &e, sizeof(e));
    return 0;
}

// VFS read errors
int trace_vfs_read_ret(struct pt_regs *ctx) {
    long ret = PT_REGS_RC(ctx);
    if (ret >= 0) return 0;
    struct f_event_t e = {};
    e.pid  = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    e.tgid = bpf_get_current_pid_tgid() >> 32;
    e.type = 2;
    bpf_get_current_comm(&e.comm, sizeof(e.comm));
    f_events.perf_submit(ctx, &e, sizeof(e));
    return 0;
}
"""

_ETYPE = {0: "blk_io_latency", 1: "vfs_write_error", 2: "vfs_read_error"}


class EventListener:
    """
    Real-mode eBPF listener for stateful workload monitoring.
    Probes: blk_account_io_start/completion, vfs_write (ret), vfs_read (ret).
    """

    def __init__(self):
        from bcc import BPF
        self.b = BPF(text=_BPF_PROGRAM)
        self.b.attach_kprobe(event="blk_account_io_start",       fn_name="trace_blk_start")
        self.b.attach_kprobe(event="blk_account_io_completion",  fn_name="trace_blk_done")
        self.b.attach_kretprobe(event="vfs_write", fn_name="trace_vfs_write_ret")
        self.b.attach_kretprobe(event="vfs_read",  fn_name="trace_vfs_read_ret")
        print("[undercurrent-f] stateful event_listener started", flush=True)

    def handle_event(self, cpu, data, size):
        ev    = self.b["f_events"].event(data)
        etype = _ETYPE.get(ev.type, "unknown")
        record = {
            "container":  ev.comm.decode("utf-8", errors="replace"),
            "pid":        ev.pid,
            "event":      etype,
            "time":       time.time(),
            "latency_us": ev.latency_ns // 1000 if ev.type == 0 else 0,
            "node_type":  "F",
        }
        print(json.dumps(record), flush=True)

    def listen(self):
        self.b["f_events"].open_perf_buffer(self.handle_event)
        while True:
            try:
                self.b.perf_buffer_poll()
            except KeyboardInterrupt:
                print("\n[undercurrent-f] stopping.", flush=True)
                break


if __name__ == "__main__":
    listener = EventListener()
    listener.listen()
