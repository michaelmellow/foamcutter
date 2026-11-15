"""Send G-code to grblHAL over the board's USB serial port (stage 1 of the built-in sender).

What it does, and nothing more: connect, poll the machine state for a live DRO,
stream a G-code program, feed hold / resume, stop, and stop on the first error.
Jogging, homing and MDI stay in ioSender for now.

**Only one program can have the port open.** Windows opens a COM port for one
program at a time: while ioSender is connected, `connect()` fails with "access
denied" (reported as "in use"), and the other way round. Disconnect one before
connecting the other. Settings changed in ioSender are kept on the board.

Protocol (Grbl 1.1 serial interface, which grblHAL keeps):

- **Lines** end with "\\n" and are answered, in order, by "ok" or "error:N".
- **Realtime bytes** act at once and get no answer, and they do not use the
  receive buffer: "?" (status report), "!" (feed hold), "~" (cycle start /
  resume), 0x18 (soft reset).
- **Character counting.** A line is sent only if every line not yet answered,
  plus this one, fits in the controller's receive buffer. The size is read from
  `$I` (`[OPT:flags,planner blocks,rx buffer,...]`; 1024 on the as-flashed
  board, firmware/board-as-flashed.txt). Grbl's 128 is used until it is known.

**Stop** is feed hold first, then soft reset once the machine reports Hold:0
(stopped). Grbl documents a reset during motion as losing position
(ALARM:3); a reset after the hold has finished does not. Not yet verified on
this board.

**Errors.** grblHAL skips a line it rejects and runs the next ones already in
its buffer. A program with a missing move would cut the wrong shape, so on the
first error the sender stops sending and holds. The operator then stops.

One worker thread owns the port. The window talks to it through methods that
queue commands, and reads `events` (a queue of `Event`) from its own timer.
"""

import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Protocol

STATUS = b"?"
FEED_HOLD = b"!"
CYCLE_START = b"~"
JOG_CANCEL = b"\x85"  # grblHAL / Grbl 1.1: stop a $J= jog at once, emptying the jog moves
SOFT_RESET = b"\x18"
# feed override realtime commands (Grbl 1.1 / grblHAL): back to 100 %, +10, -10, +1, -1 %
FEED_OV_RESET, FEED_OV_UP10, FEED_OV_DOWN10, FEED_OV_UP1, FEED_OV_DOWN1 = (bytes([b]) for b in range(0x90, 0x95))
FEED_OV_RANGE = (50, 200)  # percent offered by the Machine tab (grblHAL itself allows 10 to 200)
OV_SETTLE_SECONDS = 0.5  # ignore the reported override this long after changing it (older reports)
GRBL_RX_BUFFER = 128  # until $I gives the real size
POLL_SECONDS = 0.2  # status report interval
ANSWER_SECONDS = 3.0  # wait this long for the first status report after connecting
HOLD_SECONDS = 10.0  # stop: reset anyway if the hold has not finished by then
BUFFER_MARGIN = 2  # bytes of the receive buffer left unused
QUIET = -1  # line number of a command the sender sends for itself and does not show
MAX_RATE_SETTINGS = (110, 111, 112, 113)  # grblHAL: X, Y, Z, A max rate, mm/min


class SenderError(Exception):
    """Cannot open the port, or a program the controller cannot take."""


class Port(Protocol):
    """The part of pyserial's Serial the sender uses (tests pass a fake)."""

    @property
    def in_waiting(self) -> int: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes, /) -> int | None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Status:
    state: str  # Idle, Run, Hold:0, Hold:1, Jog, Alarm, Door:0, Check, Home, Sleep, Tool
    mpos: tuple[float, ...]  # machine position, mm
    wpos: tuple[float, ...]  # work position (MPos - WCO), mm
    feed: float | None  # mm/min, or the inverse-time rate in G93 as the controller reports it
    buffer: tuple[int, int] | None  # free planner blocks, free receive bytes
    pins: str  # input pins seen active ("Pn:" field), "" if none
    overrides: tuple[int, ...] | None = None  # feed, rapid, spindle override, percent ("Ov:" field)
    at: float = 0.0  # clock time the report was read (for speeds from position changes)


@dataclass(frozen=True)
class Event:
    """What happened, for the window. kind:

    - connected: the controller answered (text: device). lost: the connection failed or
      the controller never answered; disconnected follows. disconnected: port released.
    - status: a status report (status). log: a line from the controller. sent: a command line sent.
    - progress: program lines answered (done, total). done: the program finished.
    - error: the controller rejected a program line; it is held and only Stop is possible.
    - stopped: the program ended before its end (stop, alarm, reset or lost connection).
    """

    kind: str
    text: str = ""
    status: Status | None = None
    done: int = 0  # program lines answered "ok"
    total: int = 0


def list_ports() -> list[tuple[str, str]]:
    """(device, description) of each serial port, e.g. ("COM5", "USB Serial Device (COM5)")."""
    from serial.tools import list_ports as lp

    return [(p.device, p.description) for p in sorted(lp.comports(), key=lambda p: p.device)]


def open_serial(device: str) -> Port:
    """Open a USB serial port (the baud rate means nothing to USB CDC). Raises SenderError."""
    import serial

    try:
        return serial.Serial(device, 115200, timeout=0.02, write_timeout=2)
    except serial.SerialException as error:
        text = str(error)
        if "Access is denied" in text or "PermissionError" in text or "busy" in text.lower():
            raise SenderError(
                f"{device} is in use by another program. Disconnect ioSender (or close it) first."
            ) from error
        raise SenderError(f"cannot open {device}: {text}") from error


def override_bytes(current: int, target: int) -> bytes:
    """Realtime bytes that take the feed override from `current` to `target` percent,
    in steps of 10 then 1, never passing through values outside the two."""
    d = target - current
    tens, ones = divmod(abs(d), 10)
    if d >= 0:
        return FEED_OV_UP10 * tens + FEED_OV_UP1 * ones
    return FEED_OV_DOWN10 * tens + FEED_OV_DOWN1 * ones


def scale_feeds(gcode: str, factor: float) -> str:
    """The program with every F word multiplied by `factor` (dry runs).

    Works in both feed modes: in G93 F is 1/minutes per move, in G94 mm/min, so
    both get `factor` times faster. Rapids (G0) have no F and are unchanged. The
    controller still limits each axis to its max rate ($110-$113), so a large
    factor runs the cutting moves at the machine's limit."""
    if factor == 1:
        return gcode

    def scaled(m: re.Match[str]) -> str:
        return f"F{float(m.group(1)) * factor:.6g}"

    out = []
    for line in gcode.splitlines():
        code, sep, comment = line.partition(";")
        parts = re.split(r"(\([^)]*\))", code)  # leave comments alone
        parts = [p if p.startswith("(") else re.sub(r"F\s*([-+]?\d*\.?\d+)", scaled, p) for p in parts]
        out.append("".join(parts) + sep + comment)
    return "\n".join(out) + ("\n" if gcode.endswith("\n") else "")


def program_lines(gcode: str) -> list[tuple[int, str]]:
    """(line number in the file, line to send): comments and blank lines dropped."""
    out = []
    for number, raw in enumerate(gcode.splitlines(), start=1):
        line = re.sub(r"\([^)]*\)", "", raw).split(";", 1)[0].strip()
        if line and line != "%":
            out.append((number, line))
    return out


def parse_status(text: str, wco: tuple[float, ...] | None) -> tuple[Status, tuple[float, ...] | None]:
    """A status report "<Idle|MPos:...|FS:...>" and the work offset to use from now on.

    The controller sends WCO only now and then (and MPos or WPos, as $10 says), so
    the last WCO is kept and passed back in."""
    fields = text.strip().strip("<>").split("|")
    state = fields[0]
    mpos: tuple[float, ...] | None = None
    wpos: tuple[float, ...] | None = None
    feed = None
    buffer = None
    pins = ""
    overrides = None
    for f in fields[1:]:
        name, _, value = f.partition(":")
        if name in ("MPos", "WPos", "WCO"):
            numbers = tuple(float(v) for v in value.split(","))
            if name == "MPos":
                mpos = numbers
            elif name == "WPos":
                wpos = numbers
            else:
                wco = numbers
        elif name in ("FS", "F"):
            feed = float(value.split(",")[0])
        elif name == "Bf":
            a, b = value.split(",")[:2]
            buffer = (int(a), int(b))
        elif name == "Pn":
            pins = value
        elif name == "Ov":
            overrides = tuple(int(v) for v in value.split(","))
    offset = wco or (0.0,) * len(mpos or wpos or ())
    if mpos is not None:
        wpos = tuple(m - o for m, o in zip(mpos, offset, strict=False))
    elif wpos is not None:
        mpos = tuple(w + o for w, o in zip(wpos, offset, strict=False))
    return Status(state, mpos or (), wpos or (), feed, buffer, pins, overrides), wco


@dataclass
class _Sent:
    length: int  # bytes in the receive buffer, newline included
    line: str
    number: int  # line number in the program, 0 for a command that is not part of it


@dataclass
class _Job:
    lines: list[tuple[int, str]]
    label: str = "program"  # in the done message: "homing macro finished in 0.3 min"
    next: int = 0
    done: int = 0
    failed: str = ""  # set on the first error: nothing more is sent
    stopping: float = 0.0  # time the stop began (feed hold sent), 0 if not stopping
    started: float = field(default_factory=time.monotonic)


class Sender:
    """A connection to one controller. All methods are safe to call from the window's thread."""

    def __init__(self, port: Port, device: str = "", clock: Callable[[], float] = time.monotonic) -> None:
        self.port = port
        self.device = device
        self.events: queue.Queue[Event] = queue.Queue()
        self.rx_size = GRBL_RX_BUFFER
        self.planner_size: int | None = None  # planner blocks, from $I [OPT:...]
        self.settings: dict[int, float] = {}  # numeric $ settings read with $$ at connect ($110 = X max rate, ...)
        self._quiet = True  # the connect-time $$ is unanswered: its setting lines are not logged
        self.poll_seconds = POLL_SECONDS  # the window polls faster while the wire follows the mouse
        self.status: Status | None = None
        self._clock = clock
        self._commands: queue.Queue[tuple[str, object]] = queue.Queue()
        self._sent: deque[_Sent] = deque()
        self._outbox: deque[_Sent] = deque()  # commands waiting for room in the buffer
        self._job: _Job | None = None
        self._wco: tuple[float, ...] | None = None
        self._partial = b""
        self._closing = False
        self._answered = False
        self._status_at = 0.0  # clock time of the last status report
        self.feed_override = 100  # percent, as last sent or reported
        self._ov_changed = -1e9  # clock time the override was last sent
        self._thread = threading.Thread(target=self._run, name=f"grbl {device}", daemon=True)

    @classmethod
    def connect(cls, device: str) -> "Sender":
        """Open `device` and start talking to it. Raises SenderError if it cannot be opened;
        a "connected" or "lost" event follows once the controller answers or does not."""
        sender = cls(open_serial(device), device)
        sender.start()
        return sender

    def start(self) -> None:
        self._thread.start()

    # --- requests from the window --------------------------------------------------------

    def rapid_rate(self) -> float | None:
        """The slowest axis max rate ($110-$113), mm/min: the fastest a move of every axis can go.
        None until $$ has been read."""
        rates = [self.settings[n] for n in MAX_RATE_SETTINGS if n in self.settings]
        return min(rates) if len(rates) == len(MAX_RATE_SETTINGS) else None

    @property
    def pending(self) -> int:
        """Lines sent or queued to send that the controller has not answered yet."""
        return len(self._sent) + len(self._outbox) + self._commands.qsize()

    @property
    def running(self) -> bool:
        """A program is being sent (or held, or stopping)."""
        return self._job is not None

    def run_program(self, gcode: str, label: str = "program") -> int:
        """Start sending a program. Returns the number of lines. Raises SenderError."""
        lines = program_lines(gcode)
        if not lines:
            raise SenderError("the program has no G-code lines")
        if self._job is not None:
            raise SenderError("a program is already running")
        state = self.status.state if self.status else "unknown"
        if state != "Idle":
            raise SenderError(f"the machine must be Idle to start, not {state}")
        limit = self.rx_size - BUFFER_MARGIN
        too_long = [n for n, line in lines if len(line) + 1 > limit]
        if too_long:
            raise SenderError(f"line {too_long[0]} is longer than the controller's {self.rx_size}-byte buffer")
        self._commands.put(("run", (lines, label)))
        return len(lines)

    def command(self, line: str) -> None:
        """Send one line ($X, $I, ...) outside a program. Refused while a program runs."""
        if self._job is not None:
            raise SenderError("a program is running")
        self._commands.put(("line", line.strip()))

    def jog(self, line: str) -> None:
        """Send a `$J=...` jog. Refused while a program runs."""
        if not line.startswith("$J="):
            raise SenderError("a jog line starts with $J=")
        self.command(line)

    def cancel_jog(self) -> None:
        self._commands.put(("realtime", JOG_CANCEL))

    def hold(self) -> None:
        self._commands.put(("realtime", FEED_HOLD))

    def resume(self) -> None:
        """Cycle start. Ignored after an error: the program cannot continue then."""
        self._commands.put(("resume", None))

    def stop(self) -> None:
        """Feed hold, then soft reset once the machine has stopped; the program is abandoned."""
        self._commands.put(("stop", None))

    def set_feed_override(self, percent: int) -> None:
        """Feed override, clamped to FEED_OV_RANGE. Acts at once, also during a cut."""
        lo, hi = FEED_OV_RANGE
        self._commands.put(("feed_ov", max(lo, min(hi, int(percent)))))

    def reset(self) -> None:
        """Soft reset now. During motion this loses position (ALARM)."""
        self._commands.put(("reset", None))

    def close(self) -> None:
        """Stop the worker and release the port. A running program is stopped first."""
        if self._job is not None:
            self.stop()
            deadline = time.monotonic() + HOLD_SECONDS + 1
            while self._job is not None and self._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
        self._closing = True
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self.port.close()
        except OSError:
            pass

    # --- worker ------------------------------------------------------------------------

    def _emit(self, kind: str, text: str = "", status: Status | None = None, done: int = 0, total: int = 0) -> None:
        self.events.put(Event(kind, text, status, done, total))

    def _write(self, data: bytes) -> None:
        self.port.write(data)

    def _run(self) -> None:
        opened = self._clock()
        next_poll = 0.0
        self._outbox.append(_Sent(len("$I") + 1, "$I", 0))
        self._outbox.append(_Sent(len("$$") + 1, "$$", QUIET))  # max rates etc., not shown in the console
        try:
            while not self._closing:
                self._handle_commands()
                self._read()
                now = self._clock()
                if not self._answered and now - opened > ANSWER_SECONDS:
                    self._emit("lost", f"no answer from a grblHAL controller on {self.device or 'the port'}")
                    break
                if now >= next_poll:
                    self._write(STATUS)
                    next_poll = now + self.poll_seconds
                self._check_stop(now)
                self._fill()
        except (OSError, ValueError) as error:  # pyserial: SerialException is an OSError subclass
            if not self._closing:
                self._emit("lost", f"lost the connection: {error}")
        finally:
            if self._job is not None:
                self._job = None
                self._emit("stopped", "connection closed during the program")
            self._emit("disconnected")

    def _handle_commands(self) -> None:
        while True:
            try:
                kind, arg = self._commands.get_nowait()
            except queue.Empty:
                return
            job = self._job
            if kind == "run":
                assert isinstance(arg, tuple)
                lines, label = arg
                self._job = _Job(lines, label, started=self._clock())
                self._emit("progress", done=0, total=len(lines))
            elif kind == "line":
                assert isinstance(arg, str)
                self._outbox.append(_Sent(len(arg) + 1, arg, 0))
            elif kind == "realtime":
                assert isinstance(arg, bytes)
                self._write(arg)
            elif kind == "resume":
                if job is None or not (job.failed or job.stopping):
                    self._write(CYCLE_START)
            elif kind == "stop":
                if job is None:
                    self._write(FEED_HOLD)  # a command or jog in progress: hold it as well
                elif not job.stopping:
                    job.stopping = self._clock()
                    self._write(FEED_HOLD)
                    self._emit("log", "stop: feed hold, then reset when stopped")
            elif kind == "reset":
                self._soft_reset()
            elif kind == "feed_ov":
                assert isinstance(arg, int)
                data = override_bytes(self.feed_override, arg)
                if data:
                    self._write(data)
                    self.feed_override = arg
                    self._ov_changed = self._clock()

    def _check_stop(self, now: float) -> None:
        job = self._job
        if job is None or not job.stopping:
            return
        # only a report asked for after the hold counts: an older one may say Idle or Run
        state = self.status.state if self.status and self._status_at > job.stopping else ""
        if state in ("Hold:0", "Idle", "Alarm") or now - job.stopping > HOLD_SECONDS:
            if state not in ("Hold:0", "Idle", "Alarm"):
                self._emit("log", f"the hold did not finish in {HOLD_SECONDS:g} s (state {state}); resetting")
            self._soft_reset()

    def _soft_reset(self) -> None:
        self._write(SOFT_RESET)
        self._sent.clear()
        self._outbox.clear()
        job, self._job = self._job, None
        if job is not None:
            why = f"stopped after an error: {job.failed}" if job.failed else "stopped by the operator"
            self._emit("stopped", why, done=job.done, total=len(job.lines))

    def _fill(self) -> None:
        """Send lines while they fit in the controller's receive buffer."""
        limit = self.rx_size - BUFFER_MARGIN
        used = sum(s.length for s in self._sent)
        while True:
            if self._outbox:
                item = self._outbox[0]
            elif self._job is not None and not self._job.failed and not self._job.stopping and (
                self._job.next < len(self._job.lines)
            ):
                number, line = self._job.lines[self._job.next]
                item = _Sent(len(line) + 1, line, number)
            else:
                return
            if used + item.length > limit:
                return
            self._write(item.line.encode("ascii", "replace") + b"\n")
            self._sent.append(item)
            used += item.length
            if self._outbox and item is self._outbox[0]:
                self._outbox.popleft()
                if item.number != QUIET:
                    self._emit("sent", item.line)
            else:
                assert self._job is not None
                self._job.next += 1

    def _read(self) -> None:
        waiting = self.port.in_waiting
        data = self.port.read(max(1, waiting))
        if not data:
            return
        self._partial += data
        *lines, self._partial = self._partial.split(b"\n")
        for raw in lines:
            text = raw.decode("ascii", "replace").strip()
            if text:
                self._handle_line(text)

    def _handle_line(self, text: str) -> None:
        if text.startswith("<"):
            try:
                status, self._wco = parse_status(text, self._wco)
                self._status_at = self._clock()
                self.status = replace(status, at=self._status_at)
                if status.overrides and self._status_at - self._ov_changed > OV_SETTLE_SECONDS:
                    self.feed_override = status.overrides[0]
            except ValueError:
                self._emit("log", f"unreadable status report: {text}")
                return
            if not self._answered:
                self._answered = True
                self._emit("connected", self.device)
            self._emit("status", status=self.status)
        elif text == "ok" or text.startswith("error"):
            self._answer(text)
        elif text.startswith("ALARM"):
            self._emit("log", text)
            job, self._job = self._job, None
            if job is not None:
                self._outbox.clear()
                self._emit("stopped", f"{text} at program line {self._current_line(job)}: the controller stopped",
                           done=job.done, total=len(job.lines))
        elif text.lower().startswith("grbl"):  # welcome message: the controller has reset
            self._emit("log", text)
            self._sent.clear()
            self.feed_override = 100  # a reset sets the overrides back to 100 %
            job, self._job = self._job, None
            if job is not None:
                self._emit("stopped", "the controller reset during the program", done=job.done, total=len(job.lines))
        else:
            setting = re.fullmatch(r"\$(\d+)=(-?\d+(?:\.\d*)?)", text)
            if setting:
                self.settings[int(setting.group(1))] = float(setting.group(2))
                if self._quiet:
                    return
            if text.startswith("[OPT:"):
                parts = text[5:-1].split(",")
                if len(parts) >= 3 and parts[2].isdigit():
                    self.rx_size = int(parts[2])
                if len(parts) >= 2 and parts[1].isdigit():
                    self.planner_size = int(parts[1])
            self._emit("log", text)

    def _current_line(self, job: _Job) -> int:
        return self._sent[0].number if self._sent else (job.lines[job.next - 1][0] if job.next else 0)

    def _answer(self, text: str) -> None:
        if not self._sent:
            self._emit("log", f"{text} (not expected)")
            return
        item = self._sent.popleft()
        job = self._job
        if item.number == QUIET:
            self._quiet = False
            return
        if item.number == 0:
            self._emit("log", f"{item.line}: {text}")
            return
        if job is None:
            return
        if text == "ok":
            job.done += 1
            if job.done == len(job.lines) and not job.failed:
                self._job = None
                minutes = (self._clock() - job.started) / 60
                self._emit("done", f"{job.label} finished in {minutes:.1f} min", done=job.done, total=len(job.lines))
            else:
                self._emit("progress", done=job.done, total=len(job.lines))
            return
        if not job.failed:
            job.failed = f"{text} on line {item.number} ({item.line})"
            self._write(FEED_HOLD)
            self._emit("error", f"{job.failed}. Held: press Stop.", done=job.done, total=len(job.lines))


def iter_events(sender: Sender) -> Iterable[Event]:
    """Every event waiting now (the window calls this from a timer)."""
    while True:
        try:
            yield sender.events.get_nowait()
        except queue.Empty:
            return
