#!/usr/bin/env python3
import json
import os
import sys
import time
import shlex
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

LOG_TS_FMT = "%Y-%m-%d %H:%M:%S"


def log(msg: str, log_file: Optional[str]) -> None:
    line = f"[{datetime.now().strftime(LOG_TS_FMT)}] {msg}"
    print(line, flush=True)
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def parse_hhmm(s: str) -> Tuple[int, int]:
    hh, mm = s.strip().split(":")
    hh = int(hh); mm = int(mm)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Invalid HH:MM: {s}")
    return hh, mm


def compute_next_run(now: datetime, schedule: List[str]) -> datetime:
    candidates = []
    for t in schedule:
        hh, mm = parse_hhmm(t)
        cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        candidates.append(cand)
    return min(candidates)


def iso8601_with_tz_ms(dt: datetime) -> str:
    # match your pisugar format: ...:SS.000-05:00
    local_aware = dt.astimezone().replace(microsecond=0)
    s = local_aware.isoformat()
    if "." not in s:
        # insert .000 before timezone
        if len(s) >= 6 and s[-6] in ["+", "-"] and s[-3] == ":":
            s = s[:-6] + ".000" + s[-6:]
        else:
            s = s + ".000"
    return s


def extract_hms_from_alarm_line(line: str) -> Optional[str]:
    # "rtc_alarm_time: 2000-01-01T01:15:00.000-05:00"
    if "T" not in line:
        return None
    after_t = line.split("T", 1)[1]
    hms = after_t.split(".", 1)[0]
    if len(hms.split(":")) == 3:
        return hms
    return None


def wait_for_network(log_file: Optional[str], timeout_sec: int = 60) -> bool:
    start = time.time()

    while time.time() - start < timeout_sec:
        try:
            subprocess.check_call(
                ["ping", "-c", "1", "-W", "1", "192.168.76.71"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            log("Network reachable.", log_file)
            return True

        except subprocess.CalledProcessError:
            log("Waiting for network...", log_file)

        time.sleep(3)

    log("Network wait timeout.", log_file)
    return False


def wait_for_time_sync(log_file: Optional[str], timeout_sec: int = 120) -> bool:
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            out = subprocess.check_output(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                text=True
            ).strip()
            log(f"NTPSynchronized={out}", log_file)
            if out.lower() == "yes":
                return True
        except Exception as e:
            log(f"timedatectl check failed: {e}", log_file)
        time.sleep(5)
    return False


@dataclass
class PiSugarCfg:
    uds_path: str
    timeout_sec: int


@dataclass
class TaskCfg:
    command: str
    timeout_sec: int


@dataclass
class ShutdownCfg:
    command: str
    shutdown_even_if_alarm_set_failed: bool


@dataclass
class Config:
    schedule: List[str]
    repeat_mask: int
    pisugar: PiSugarCfg
    task: TaskCfg
    shutdown: ShutdownCfg
    log_file: Optional[str]


class PiSugarClient:
    def __init__(self, cfg: PiSugarCfg, log_file: Optional[str]):
        self.cfg = cfg
        self.log_file = log_file

    def cmd(self, command: str) -> str:
        command = command.strip() + "\n"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.cfg.timeout_sec)
        try:
            s.connect(self.cfg.uds_path)
            s.sendall(command.encode("utf-8"))
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            resp = data.decode("utf-8", errors="replace").strip()
            log(f"[pisugar] {command.strip()} -> {resp}", self.log_file)
            return resp
        finally:
            try:
                s.close()
            except Exception:
                pass

    def health_check_and_RTC_restore(self) -> None:
        self.cmd("get model")
        self.cmd("get rtc_time")
        resp = self.cmd("rtc_rtc2pi")
        if "done" not in resp:
            raise RuntimeError(f"rtc_rtc2pi failed: {resp}")

    def set_alarm(self, iso_time: str, repeat_mask: int) -> None:
        resp = self.cmd(f"rtc_alarm_set {iso_time} {repeat_mask}")
        if "done" not in resp:
            raise RuntimeError(resp)

    def get_alarm_time(self) -> str:
        return self.cmd("get rtc_alarm_time")

    def get_alarm_repeat(self) -> str:
        return self.cmd("get alarm_repeat")

    def rtc_pi2rtc(self) -> str:
        return self.cmd("rtc_pi2rtc")


def run_task(cmd: str, timeout_sec: int, log_file: Optional[str]) -> int:
    log(f"Run task: {cmd}", log_file)
    proc = subprocess.Popen(
        shlex.split(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    start = time.time()
    try:
        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                log(f"[task] {line.rstrip()}", log_file)
            if proc.poll() is not None:
                break
            if timeout_sec > 0 and (time.time() - start) > timeout_sec:
                log(f"Task timeout after {timeout_sec}s; terminating.", log_file)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return 124
        return proc.returncode or 0
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        schedule=raw["schedule"],
        repeat_mask=int(raw.get("repeat_mask", 127)),
        pisugar=PiSugarCfg(
            uds_path=raw["pisugar"].get("uds_path", "/tmp/pisugar-server.sock"),
            timeout_sec=int(raw["pisugar"].get("timeout_sec", 2)),
        ),
        task=TaskCfg(
            command=raw["task"]["command"],
            timeout_sec=int(raw["task"].get("timeout_sec", 0)),
        ),
        shutdown=ShutdownCfg(
            command=raw["shutdown"].get("command", "/sbin/shutdown -h now"),
            shutdown_even_if_alarm_set_failed=bool(raw["shutdown"].get("shutdown_even_if_alarm_set_failed", False)),
        ),
        log_file=raw.get("log_file"),
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /etc/pisched/config.json")
        return 2

    cfg = load_config(sys.argv[1])
    log_file = cfg.log_file

    log("Boot sequence started.", log_file)

    client = PiSugarClient(cfg.pisugar, log_file)

    # 0) Health check
    try:
        client.health_check_and_RTC_restore()
    except Exception as e:
        log(f"PiSugar health check failed: {e}", log_file)
        return 10

    # 0.1 Wait network
    net_ok = wait_for_network(log_file, timeout_sec=30)
    if net_ok:
        log("Network ready.", log_file)
    else:
        log("Network not ready but continue anyway.", log_file)

    # 0.2) Wait for system time sync
    synced = wait_for_time_sync(log_file, timeout_sec=30)
    if synced:
        log("System time synchronized.", log_file)
        # 0.3) Sync Pi time -> RTC
        try:
            resp = client.rtc_pi2rtc()
            log(f"RTC sync (pi->rtc): {resp}", log_file)
        except Exception as e:
            log(f"RTC sync (pi->rtc) failed: {e}", log_file)
    else:
        log("System time not synchronized within timeout; continue anyway.", log_file)

    now = datetime.now()
    next_dt = compute_next_run(now, cfg.schedule)
    next_iso = iso8601_with_tz_ms(next_dt)
    expected_hms = next_dt.strftime("%H:%M:%S")

    log(f"Fully Booted at: {now.strftime(LOG_TS_FMT)}", log_file)
    log(f"Next schedule: {next_dt.strftime(LOG_TS_FMT)} (iso={next_iso})", log_file)

    # 1) Run task
    rc = run_task(cfg.task.command, cfg.task.timeout_sec, log_file)
    log(f"Task rc={rc}", log_file)

    # 2) Set next alarm + verify
    alarm_ok = False
    try:
        client.set_alarm(next_iso, cfg.repeat_mask)

        alarm_line = client.get_alarm_time()
        repeat_line = client.get_alarm_repeat()

        got_hms = extract_hms_from_alarm_line(alarm_line)
        if got_hms != expected_hms:
            raise RuntimeError(f"alarm verify mismatch: got {got_hms}, expected {expected_hms} ({alarm_line})")

        if f"alarm_repeat: {cfg.repeat_mask}" not in repeat_line:
            raise RuntimeError(f"repeat verify mismatch: {repeat_line}")

        alarm_ok = True
        log("Alarm set+verify OK.", log_file)
    except Exception as e:
        log(f"Alarm set/verify FAILED: {e}", log_file)

    # 3) Shutdown immediately
    DEBUG_PATHS = ["/boot/DEBUG", "/boot/firmware/DEBUG"]
    if any(os.path.exists(p) for p in DEBUG_PATHS):
        log("Debug mode enabled, skip shutdown", log_file)
    elif alarm_ok or cfg.shutdown.shutdown_even_if_alarm_set_failed:
        log("Shutdown now.", log_file)
        os.system(cfg.shutdown.command)
    else:
        log("Skip shutdown (alarm failed).", log_file)

    return rc


if __name__ == "__main__":
    sys.exit(main())
