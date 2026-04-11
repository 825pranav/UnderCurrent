import ctypes
import json
import time
from bcc import BPF

bpf_program = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

struct event_t {
    u32 pid;
    u32 tgid;
    char comm[TASK_COMM_LEN];
    u32 type; // 0 = process_exit, 1 = tcp_connect
};

BPF_PERF_OUTPUT(events);

TRACEPOINT_PROBE(sched, sched_process_exit) {
    struct event_t event = {};
    event.pid  = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    event.tgid = bpf_get_current_pid_tgid() >> 32;
    event.type = 0;
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    events.perf_submit(args, &event, sizeof(event));
    return 0;
}

int trace_tcp_connect(struct pt_regs *ctx) {
    struct event_t event = {};
    event.pid  = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    event.tgid = bpf_get_current_pid_tgid() >> 32;
    event.type = 1;
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}
"""

# Map kernel comm strings → canonical Docker container name.
# bpf_get_current_comm() returns the task's comm, which differs from the
# Docker container name for some services:
#   redis  → process is "redis-server"  (not "redis")
#   mysql  → process is "mysqld"        (not "mysql")
# Events from any comm not in this map are non-container processes and are
# silently dropped so they never appear in the output stream.
_DOCKER_COMM_MAP = {
    "nginx":        "nginx",
    "postgres":     "postgres",
    "redis-server": "redis",
    "mysqld":       "mysql",
}


class EventListener:
    def __init__(self):
        self.b = BPF(text=bpf_program)
        self.b.attach_kprobe(event="tcp_connect", fn_name="trace_tcp_connect")
        print("[undercurrent] event_listener started", flush=True)

    def handle_event(self, cpu, data, size):
        event = self.b["events"].event(data)
        comm = event.comm.decode("utf-8", errors="replace").rstrip("\x00")
        container = _DOCKER_COMM_MAP.get(comm)
        if container is None:
            return  # not a Docker container process — discard
        event_type = "process_exit" if event.type == 0 else "tcp_connect"
        log = {
            "container": container,
            "pid":       event.pid,
            "tgid":      event.tgid,
            "event":     event_type,
            "time":      time.time()
        }
        print(json.dumps(log), flush=True)

    def listen(self):
        self.b["events"].open_perf_buffer(self.handle_event)
        while True:
            try:
                self.b.perf_buffer_poll()
            except KeyboardInterrupt:
                print("\n[undercurrent] stopping.", flush=True)
                break

if __name__ == "__main__":
    listener = EventListener()
    listener.listen()
