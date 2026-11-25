"""The built-in sender (foamdxf.sender) against a fake grblHAL controller.

The fake keeps a receive buffer of the size it reports in $I and fails the test
if the sender ever overfills it, answers lines in order (one per read, so the
buffer really fills), and acts on the realtime bytes ?, !, ~ and 0x18 as Grbl
documents them.
"""

import queue
import re
import threading
import time

import pytest

from foamdxf import sender as sender_mod
from foamdxf.machine import (
    DEFAULT_HOMING_MACRO,
    jog_line,
    move_line,
    tower_positions,
    tower_targets,
    wire_speeds,
)
from foamdxf.sender import (
    FEED_HOLD,
    FEED_OV_DOWN1,
    FEED_OV_DOWN10,
    FEED_OV_UP1,
    FEED_OV_UP10,
    JOG_CANCEL,
    SOFT_RESET,
    Event,
    Sender,
    SenderError,
    Status,
    iter_events,
    override_bytes,
    parse_status,
    program_lines,
    scale_feeds,
)


class FakeGrbl:
    def __init__(self, rx: int = 64, answer: bool = True, reject: str = "", alarm: str = "",
                 track: dict[str, float] | None = None) -> None:
        self.rx = rx
        # track: start position by letter; moves (G90/G91 G0/G1, $J=) then change it. Without it the
        # carriages report X = 1 + 0.1 per move run, Y Z A fixed (enough for the streaming tests)
        self.pos = dict(track) if track is not None else None
        self.relative = False
        self.answer = answer
        self.reject = reject  # a line answered with error:20
        self.alarm = alarm  # a line after which the controller raises ALARM:1
        self.state = "Idle"
        self.buffer = b""  # received, not yet answered
        self.out = b""  # waiting to be read by the sender
        self.lines: list[str] = []  # every line received, in order
        self.realtime: list[tuple[bytes, str]] = []  # (byte, state when it arrived), except "?"
        self.most = 0  # largest buffer fill seen
        self.planner = 0  # moves queued in the planner, not yet run
        self.settings = {100: 800.0, 110: 3000.0, 111: 3000.0, 112: 2500.0, 113: 3000.0}  # answered to $$
        self.moves = 0  # moves run
        self.ov = 100  # feed override, percent
        self.lock = threading.Lock()
        self.closed = False

    # --- the Port interface ---
    @property
    def in_waiting(self) -> int:
        return len(self.out)

    def write(self, data: bytes, /) -> int:
        with self.lock:
            for byte in data:
                b = bytes([byte])
                if b == b"?":
                    if self.answer:
                        if self.pos is not None:
                            x, y, z, a = (self.pos[k] for k in "XYZA")
                        else:
                            x, y, z, a = 1 + 0.1 * self.moves, 2.0, 3.0, 4.0  # 0.1 mm per move
                        free = 100 - self.planner  # planner blocks free ($I says 100)
                        self.out += (f"<{self.state}|MPos:{x:.3f},{y:.3f},{z:.3f},{a:.3f}|Bf:{free},{self.rx}"
                                     f"|FS:0,0|Ov:{self.ov},100,100>\n").encode()
                elif 0x90 <= byte <= 0x94:  # feed override
                    self.realtime.append((b, self.state))
                    step = {0x90: 100 - self.ov, 0x91: 10, 0x92: -10, 0x93: 1, 0x94: -1}[byte]
                    self.ov = max(10, min(200, self.ov + step))
                elif b == JOG_CANCEL:
                    self.realtime.append((b, self.state))
                elif b in (FEED_HOLD, b"~", SOFT_RESET):
                    self.realtime.append((b, self.state))
                    if b == FEED_HOLD and self.state == "Run":
                        self.state = "Hold:0"
                    elif b == b"~" and self.state.startswith("Hold"):
                        self.state = "Run"
                    elif b == SOFT_RESET:
                        self.state = "Alarm" if self.state == "Run" else "Idle"
                        self.buffer = b""
                        self.planner = 0
                        self.ov = 100
                        self.out += b"GrblHAL 1.1f ['$' or '$HELP' for help]\n"
                else:
                    self.buffer += b
                    self.most = max(self.most, len(self.buffer))
                    assert len(self.buffer) <= self.rx, "receive buffer overfilled"
        return len(data)

    def read(self, size: int = 1) -> bytes:
        with self.lock:
            self._work()
            data, self.out = self.out[:size], self.out[size:]
        if not data:
            time.sleep(0.001)
        return data

    def close(self) -> None:
        self.closed = True

    # --- the controller ---
    def _work(self) -> None:
        """Run one queued move, then take one line from the buffer."""
        if self.state.startswith("Hold") or self.state == "Alarm":
            return
        if self.planner:
            self.planner -= 1
            self.moves += 1
        if self.state == "Run" and not self.planner:
            self.state = "Idle"
        if b"\n" not in self.buffer or self.planner >= 16:
            return
        raw, self.buffer = self.buffer.split(b"\n", 1)
        line = raw.decode()
        self.lines.append(line)
        if self.pos is not None:
            self._move(line)
        if line == "$$":
            self.out += "".join(f"${n}={v:.3f}\n" for n, v in self.settings.items()).encode() + b"ok\n"
        elif line == "$I":
            self.out += f"[VER:1.1f.20260622:]\n[OPT:VNMSL,100,{self.rx},4,0]\nok\n".encode()
        elif line == self.reject:
            self.out += b"error:20\n"
        elif line == self.alarm:
            self.state = "Alarm"
            self.out += b"ALARM:1\n"
        else:
            self.out += b"ok\n"
            if not line.startswith("$"):
                self.planner += 1  # a move: the machine runs until the planner is empty
                self.state = "Run"


    def _move(self, line: str) -> None:
        jog = line.startswith("$J=")
        relative = self.relative
        for word in line.split():
            if word.endswith("G91"):
                relative = True
            elif word.endswith("G90"):
                relative = False
        if not jog:
            self.relative = relative  # a jog's G90/G91 is for that jog only
        if not (jog or line.startswith(("G0", "G1"))):
            return
        assert self.pos is not None
        for letter, value in re.findall(r"([XYZA])(-?\d+(?:\.\d*)?)", line):
            self.pos[letter] = self.pos[letter] + float(value) if relative else float(value)


def program(n: int) -> str:
    head = "(test program)\nG21\nG90\nG93\n"
    return head + "\n".join(f"G1 X{i:.4f} Y{i / 2:.4f} Z{i:.4f} A{i / 2:.4f} F100.000" for i in range(n)) + "\nG94\n"


def collect(s: Sender, until: str, timeout: float = 10.0) -> list[Event]:
    """Events until one of kind `until` (inclusive)."""
    got: list[Event] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            e = s.events.get(timeout=0.05)
        except queue.Empty:
            continue
        got.append(e)
        if e.kind == until:
            return got
    raise AssertionError(f"no {until} event; got {[(e.kind, e.text) for e in got if e.kind != 'status']}")


def connected(fake: FakeGrbl) -> Sender:
    s = Sender(fake, "FAKE")
    s.start()
    collect(s, "connected")
    end = time.monotonic() + 5
    while s.rx_size != fake.rx and time.monotonic() < end:  # $I answered
        time.sleep(0.01)
    return s


def wait_idle(s: Sender) -> None:
    end = time.monotonic() + 5
    while (s.status is None or s.status.state != "Idle") and time.monotonic() < end:
        time.sleep(0.01)


def test_status_report_with_work_offset() -> None:
    st, wco = parse_status("<Idle|MPos:10.000,20.000,30.000,40.000|Bf:99,1023|FS:0,0|WCO:1,2,3,4>", None)
    assert st.state == "Idle"
    assert st.mpos == (10, 20, 30, 40)
    assert st.wpos == (9, 18, 27, 36)
    assert st.buffer == (99, 1023)
    assert wco == (1, 2, 3, 4)
    # WCO is sent only now and then: the last one keeps applying
    st2, wco2 = parse_status("<Run|MPos:11.000,20.000,30.000,40.000|FS:120.5,0|Pn:H>", wco)
    assert st2.wpos == (10, 18, 27, 36)
    assert st2.feed == 120.5
    assert st2.pins == "H"
    assert wco2 == wco
    st3, _ = parse_status("<Hold:0|WPos:1.000,2.000,3.000,4.000>", (1, 1, 1, 1))
    assert st3.state == "Hold:0"
    assert st3.mpos == (2, 3, 4, 5)


def test_program_lines_drop_comments_and_blanks() -> None:
    text = "(foamcam 0.1.0)\n\nG21 ; mm\nG1 X1 (cut) Y2 F10\n%\nG94\n"
    assert program_lines(text) == [(3, "G21"), (4, "G1 X1  Y2 F10"), (6, "G94")]


def test_streams_a_program_without_overfilling_the_buffer() -> None:
    fake = FakeGrbl(rx=64)
    s = connected(fake)
    try:
        assert s.rx_size == 64  # read from $I
        text = program(300)
        total = s.run_program(text)
        events = collect(s, "done")
        assert fake.planner == 0 and fake.state == "Idle", "done only once the moves have run, not when queued"
        assert total == 304
        assert fake.lines[:2] == ["$I", "$$"]  # what the sender asks at connect
        assert fake.lines[2:] == [line for _, line in program_lines(text)]  # all, in order
        assert fake.most > 64 - 30, "the buffer should actually fill, not one line at a time"
        progress = [e for e in events if e.kind == "progress"]
        assert progress[-1].done == 304 and progress[-1].total == 304  # every line answered, then done
        assert not s.running
    finally:
        s.close()
    assert fake.closed


def test_error_holds_and_sends_nothing_more_then_stop_resets() -> None:
    fake = FakeGrbl(rx=128)
    text = program(200)
    bad = program_lines(text)[50][1]
    fake.reject = bad
    s = connected(fake)
    try:
        s.run_program(text)
        events = collect(s, "error")
        assert "error:20" in events[-1].text and "line" in events[-1].text
        assert s.running
        sent_then = len(fake.lines) + fake.buffer.count(b"\n")
        time.sleep(0.3)
        assert len(fake.lines) + fake.buffer.count(b"\n") == sent_then, "nothing sent after the error"
        assert (FEED_HOLD, "Run") in fake.realtime or (FEED_HOLD, "Idle") in fake.realtime
        s.resume()  # refused after an error
        time.sleep(0.1)
        assert all(b != b"~" for b, _ in fake.realtime)
        s.stop()
        stopped = collect(s, "stopped")[-1]
        assert "error" in stopped.text
        kinds = [b for b, _ in fake.realtime]
        assert SOFT_RESET in kinds
        assert not s.running
    finally:
        s.close()


def test_stop_holds_first_and_resets_only_when_stopped() -> None:
    fake = FakeGrbl(rx=128)
    s = connected(fake)
    try:
        s.run_program(program(5000))
        while collect(s, "progress")[-1].done < 20:  # moving
            pass
        s.stop()
        collect(s, "stopped")
        bytes_, states = zip(*fake.realtime, strict=True)
        assert bytes_.index(FEED_HOLD) < bytes_.index(SOFT_RESET)
        assert states[bytes_.index(SOFT_RESET)] == "Hold:0", "reset only after the hold finished"
        wait_idle(s)
        assert s.status is not None and s.status.state == "Idle"  # no ALARM: position kept
        assert len(fake.lines) < 5000
    finally:
        s.close()


def test_hold_and_resume() -> None:
    fake = FakeGrbl(rx=128)
    s = connected(fake)
    try:
        s.run_program(program(3000))
        while collect(s, "progress")[-1].done < 20:  # moving
            pass
        s.hold()
        end = time.monotonic() + 5
        while fake.state != "Hold:0" and time.monotonic() < end:
            time.sleep(0.01)
        assert fake.state == "Hold:0"
        s.resume()
        collect(s, "done", timeout=30)
    finally:
        s.close()


def test_alarm_ends_the_program() -> None:
    fake = FakeGrbl(rx=128)
    text = program(100)
    fake.alarm = program_lines(text)[20][1]
    s = connected(fake)
    try:
        s.run_program(text)
        stopped = collect(s, "stopped")[-1]
        assert "ALARM:1" in stopped.text
        assert not s.running
    finally:
        s.close()


def test_start_refused_unless_idle_or_empty() -> None:
    fake = FakeGrbl()
    s = connected(fake)
    try:
        with pytest.raises(SenderError, match="no G-code"):
            s.run_program("(only a comment)\n")
        fake.state = "Alarm"
        end = time.monotonic() + 5
        while (s.status is None or s.status.state != "Alarm") and time.monotonic() < end:
            time.sleep(0.01)
        with pytest.raises(SenderError, match="Idle"):
            s.run_program(program(3))
        s.command("$X")
    finally:
        s.close()


def test_no_answer_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sender_mod, "ANSWER_SECONDS", 0.3)
    s = Sender(FakeGrbl(answer=False), "COM9")
    s.start()
    events = collect(s, "disconnected")
    assert any(e.kind == "lost" and "no answer" in e.text and "COM9" in e.text for e in events)
    s.close()


def test_port_in_use_names_iosender(monkeypatch: pytest.MonkeyPatch) -> None:
    import serial

    def busy(*_a: object, **_k: object) -> None:
        raise serial.SerialException("could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)")

    monkeypatch.setattr(serial, "Serial", busy)
    with pytest.raises(SenderError, match="in use by another program.*ioSender"):
        Sender.connect("COM5")


# --- the Machine tab of the window ----------------------------------------------------


@pytest.fixture
def window(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    pytest.importorskip("ezdxf")
    import tkinter as tk

    from foamdxf import gui

    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("foamdxf.machine.MACHINE_FILE", tmp_path / "machine.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    win = gui.SlicerWindow(root)
    yield win
    win.machine.disconnect()
    root.destroy()


def pump(win, done, timeout: float = 20.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < end, "timed out"
        win.root.update()
        time.sleep(0.005)


def test_machine_tab_sends_the_previewed_cut(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    values = {"root": repo / "examples/dxf/dryrun-root.dxf", "tip": repo / "examples/dxf/dryrun-tip.dxf",
              "profile": repo / "examples/profiles/real-machine.yaml", "span": "400", "stock_length": "220",
              "stock_height": "40", "out": tmp_path}
    for key, value in values.items():
        window.vars[key].set(str(value))
    m = window.machine
    m.refresh()
    assert "Preview" in m.program_note.get()
    assert window.preview()
    assert m.program_note.get().startswith("dryrun-root-dryrun-tip.nc:")

    fake = FakeGrbl(rx=1024)

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: "Connected to FAKE1" in m.conn_note.get())
    pump(window, lambda: m.axis_vars[0][1].get().strip() == "1.000")  # DRO from the status report
    # controller order, each labelled with the tower axis the profile drives with it
    assert [v[0].get() for v in m.axis_vars] == ["X", "Y", "Z", "A"]
    letters = window.geometry.letters if window.geometry else ()
    assert m.axis_vars[0][2].get().startswith(f"  axis {letters.index('X') + 1} ")
    assert "!disabled" in m.start_button.state() or not m.start_button.instate(["disabled"])

    monkeypatch.setattr("tkinter.messagebox.askokcancel", lambda *a, **k: True)
    m.start()
    pump(window, lambda: m.progress_note.get().startswith("Finished"))
    assert window.result is not None
    assert fake.lines[2:] == [line for _, line in program_lines(window.result.gcode)]

    window.vars["speed"].set("120")  # the preview is now stale: nothing to send
    assert "changed since the last Preview" in m.program_note.get()
    assert m.start_button.instate(["disabled"])

    assert m.disconnect()
    assert fake.closed and m.sender is None
    assert m.state_label.cget("text") == "OFFLINE"


# --- feed override, dry runs, wire speeds ----------------------------------------------


def test_override_bytes_step_by_tens_then_ones() -> None:
    assert override_bytes(100, 150) == FEED_OV_UP10 * 5
    assert override_bytes(150, 123) == FEED_OV_DOWN10 * 2 + FEED_OV_DOWN1 * 7
    assert override_bytes(95, 100) == FEED_OV_UP1 * 5
    assert override_bytes(120, 120) == b""


def test_status_report_override_field() -> None:
    st, _ = parse_status("<Run|MPos:0,0,0,0|FS:300,0|Ov:150,100,100>", None)
    assert st.overrides == (150, 100, 100)


def test_dry_run_scales_every_feed_and_nothing_else() -> None:
    text = "(F in G93 is 1/min)\nG93\nG0 X10 Y10\nG1 X1 Y2 F171.157\nG1 X2 F2.15866 ; F9 comment\nG94\n"
    out = scale_feeds(text, 3)
    assert out.splitlines() == [
        "(F in G93 is 1/min)",
        "G93",
        "G0 X10 Y10",
        "G1 X1 Y2 F513.471",
        "G1 X2 F6.47598 ; F9 comment",
        "G94",
    ]
    assert scale_feeds(text, 1) == text


def test_feed_override_reaches_the_controller_and_clamps() -> None:
    fake = FakeGrbl(rx=128)
    s = connected(fake)
    try:
        s.set_feed_override(150)
        s.set_feed_override(135)
        end = time.monotonic() + 5
        while fake.ov != 135 and time.monotonic() < end:
            time.sleep(0.01)
        assert fake.ov == 135
        s.set_feed_override(500)  # clamped to 200
        s.set_feed_override(5)  # clamped to 50
        end = time.monotonic() + 5
        while fake.ov != 50 and time.monotonic() < end:
            time.sleep(0.01)
        assert fake.ov == 50
        time.sleep(0.8)  # reports after the change keep it
        assert s.feed_override == 50
        assert s.status is not None and s.status.overrides == (50, 100, 100)
    finally:
        s.close()


def test_tower_positions_follow_the_profile_letters() -> None:
    # the real profile drives axis 1 with Z: positions arrive as X Y Z A
    wpos = (10.0, 20.0, 30.0, 40.0)
    assert tower_positions(wpos, ("Z", "A", "X", "Y")) == ((30, 40), (10, 20))
    assert tower_positions(wpos, ("X", "Y", "Z", "A")) == ((10, 20), (30, 40))
    assert tower_positions(wpos[:2], ("X", "Y", "Z", "A")) is None


def test_wire_speed_at_the_faces() -> None:
    letters = ("X", "Y", "Z", "A")
    before = Status("Run", (0, 0, 0, 0), (0, 0, 0, 0), None, None, "", at=10.0)
    # in 0.5 s tower 1-2 moves 1 mm in x, tower 3-4 moves 3 mm: the wire pivots
    after = Status("Run", (1, 0, 3, 0), (1, 0, 3, 0), None, None, "", at=10.5)
    speeds = dict(wire_speeds(before, after, letters, (0.25, 0.75)) or [])
    assert speeds["towers 1-2"] == pytest.approx(120)  # 1 mm in 0.5 s = 120 mm/min
    assert speeds["3-4"] == pytest.approx(360)
    assert speeds["root face"] == pytest.approx(180)  # a quarter of the way: 1.5 mm
    assert speeds["tip face"] == pytest.approx(300)
    late = Status("Run", (2, 0, 3, 0), (2, 0, 3, 0), None, None, "", at=12.0)
    assert wire_speeds(after, late, letters, None) is None  # reports too far apart


def test_window_dry_run_live_section_and_override(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    values = {"root": repo / "examples/dxf/dryrun-root.dxf", "tip": repo / "examples/dxf/dryrun-tip.dxf",
              "profile": repo / "examples/profiles/real-machine.yaml", "span": "400", "stock_length": "220",
              "stock_height": "40", "out": tmp_path, "source": "dxf"}
    for key, value in values.items():
        window.vars[key].set(str(value))
    assert window.preview()
    g, result = window.geometry, window.result
    assert g is not None and result is not None

    # the section frame is the G-code's work frame: the first feed move goes to the preview's
    # second carriage position (after the entry point), so live positions land on the drawing
    first = next(line for line in result.gcode.splitlines() if line.startswith("G1"))
    words = {k: float(v) for k, v in re.findall(r"([XYZA])(-?[\d.]+)", first)}
    l1, l2, l3, l4 = g.letters
    assert (words[l1], words[l2]) == pytest.approx(g.tower_12[1], abs=1e-3)
    assert (words[l3], words[l4]) == pytest.approx(g.tower_34[1], abs=1e-3)

    m = window.machine
    fake = FakeGrbl(rx=1024)

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: window.live is not None)
    assert window.canvas.find_withtag("live"), "live markers on the section"

    m.dry_run.set(True)
    m.dry_factor.set("5")
    m.refresh()
    assert "DRY RUN x5" in m.program_note.get()
    assert m.start_button.cget("text") == "Start DRY RUN"
    texts: list[str] = []
    def confirm(_title: str, text: str, **_k: object) -> bool:
        texts.append(text)
        return True

    monkeypatch.setattr("tkinter.messagebox.askokcancel", confirm)
    m.start()
    assert "heat is OFF" in texts[0] and "NO foam" in texts[0]
    pump(window, lambda: "root face" in m.dro_speed.get())  # wire speeds while moving
    pump(window, lambda: m.progress_note.get().startswith("Finished"))
    sent = [line for line in fake.lines[2:] if "F" in line]
    expected = [line for _, line in program_lines(scale_feeds(result.gcode, 5)) if "F" in line]
    assert sent == expected and sent != [line for _, line in program_lines(result.gcode) if "F" in line]

    m._override_to(140)
    pump(window, lambda: fake.ov == 140)
    pump(window, lambda: "override 140 %" in m.dro_detail.get())
    m._override_to(100)
    pump(window, lambda: fake.ov == 100)

    assert m.disconnect()
    assert window.live is None and not window.canvas.find_withtag("live")


# --- stage 2: jog, MDI, homing macro, go to an edge -------------------------------------


def test_jog_line_stays_inside_the_travel() -> None:
    travel = {"X": (0.0, 300.0), "Y": (0.0, 280.0), "Z": (0.0, 300.0), "A": (0.0, 280.0)}
    wpos = (1.0, 2.0, 3.0, 4.0)  # X Y Z A
    assert jog_line({"Z": 10, "X": 10}, wpos, travel, 600) == ("$J=G91 G21 Z10.000 X10.000 F600", "")
    # both towers together: shortened by the same factor (X has 1 mm left, Z 3 mm), so the wire stays square
    line, note = jog_line({"Z": -50, "X": -50}, wpos, travel, 600)
    assert line == "$J=G91 G21 Z-1.000 X-1.000 F600" and "shortened" in note
    assert jog_line({"X": -5}, (0.0, 0, 0, 0), travel, 600)[0] is None  # already at min
    assert jog_line({"Y": 500}, wpos, None, 300) == ("$J=G91 G21 Y500.000 F300", "")  # no travel known
    assert jog_line({"X": 1}, wpos, travel, 0)[0] is None


def test_jog_and_jog_cancel_reach_the_controller() -> None:
    fake = FakeGrbl(rx=128)
    s = connected(fake)
    try:
        s.jog("$J=G91 G21 X1.000 F600")
        s.cancel_jog()
        with pytest.raises(SenderError):
            s.jog("G0 X10")  # only jog lines
        end = time.monotonic() + 5
        while "$J=G91 G21 X1.000 F600" not in fake.lines and time.monotonic() < end:
            time.sleep(0.01)
        assert "$J=G91 G21 X1.000 F600" in fake.lines
        assert any(b == JOG_CANCEL for b, _ in fake.realtime)
    finally:
        s.close()


def test_window_jog_mdi_homing_and_edge(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json
    from pathlib import Path

    from foamdxf.pipeline import alignment_edges

    repo = Path(__file__).resolve().parents[2]
    values = {"root": repo / "examples/dxf/dryrun-root.dxf", "tip": repo / "examples/dxf/dryrun-tip.dxf",
              "profile": repo / "examples/profiles/real-machine.yaml", "span": "400", "stock_length": "220",
              "stock_height": "40", "out": tmp_path, "source": "dxf"}
    for key, value in values.items():
        window.vars[key].set(str(value))
    assert window.preview()
    m = window.machine
    # the section view is a tab beside the console, inside the Machine tab
    assert str(window.section_frame) in m.views.tabs() and str(m.console) in m.views.tabs()
    assert str(window.section_frame) not in window.tabs.tabs()
    assert window._travel() == {"Z": (0.0, 300.0), "A": (0.0, 280.0), "X": (0.0, 300.0), "Y": (0.0, 280.0)}

    fake = FakeGrbl(rx=1024)

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: window.live is not None and m.jog_buttons[0].instate(["!disabled"]))
    monkeypatch.setattr("tkinter.messagebox.askokcancel", lambda *a, **k: True)

    # jog: both towers horizontal -, 50 mm from Z 3 / X 1: shortened to 1 mm each
    m.jog_step.set("50")
    m.jog_feed.set("600")
    m.jog((0, 2), -1)
    pump(window, lambda: "$J=G91 G21 Z-1.000 X-1.000 F600" in fake.lines)
    assert "shortened" in m.jog_note.get()
    m.jog_step.set("1")
    m.jog((3,), 1)  # axis 4 = Y up
    pump(window, lambda: "$J=G91 G21 Y1.000 F600" in fake.lines)
    assert m.jog_rows[0].get().startswith("Both towers (Z+X")

    # MDI with history
    m.mdi_var.set("G4 P0")
    m.send_mdi()
    pump(window, lambda: "G4 P0" in fake.lines)
    assert m.mdi_var.get() == ""
    m._mdi_recall(-1)
    assert m.mdi_var.get() == "G4 P0"

    # homing macro runs like a program
    m.home()
    macro = [line for _, line in program_lines(DEFAULT_HOMING_MACRO)]
    pump(window, lambda: m.progress_note.get().startswith("Finished: homing macro"))
    assert fake.lines[-len(macro):] == macro
    assert "heat off" not in m.progress_note.get()  # only after a cut

    # Align tab: go to an edge
    assert window.geometry is not None
    edge = alignment_edges(window.geometry)[0]
    window._go_to_edge(edge)
    pump(window, lambda: m.progress_note.get().startswith("Finished: wire to the"))
    assert fake.lines[-3:] == list(edge.gcode)

    assert m.disconnect()
    saved = json.loads((tmp_path / "machine.json").read_text())
    assert saved["mdi_history"] == ["G4 P0"] and saved["jog_step"] == "1"
    assert saved["homing_macro"].startswith("M906")


# --- section view: zoom, pan, click and drag to move the towers ------------------------


def test_move_line_and_tower_targets() -> None:
    letters = ("Z", "A", "X", "Y")
    assert tower_targets(letters, 100, 30) == {"Z": 100, "A": 30, "X": 100, "Y": 30}
    assert tower_targets(letters, 100, 30, (1,)) == {"X": 100, "Y": 30}  # X held: the X/Y tower only
    travel = {"Z": (0.0, 300.0), "A": (0.0, 280.0), "X": (0.0, 300.0), "Y": (0.0, 280.0)}
    assert move_line({"X": 100, "Y": 30}, travel, 600) == ("$J=G90 G21 X100.000 Y30.000 F600", "")
    line, note = move_line({"X": -5, "Y": 400}, travel, 600)
    assert line == "$J=G90 G21 X0.000 Y280.000 F600" and "inside the travel" in note
    assert move_line({"X": 1}, None, 0)[0] is None


def test_section_view_zoom_pan_click_and_drag(window, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path
    from types import SimpleNamespace

    repo = Path(__file__).resolve().parents[2]
    values = {"root": repo / "examples/dxf/dryrun-root.dxf", "tip": repo / "examples/dxf/dryrun-tip.dxf",
              "profile": repo / "examples/profiles/real-machine.yaml", "span": "400", "stock_length": "220",
              "stock_height": "40", "out": tmp_path, "source": "dxf"}
    for key, value in values.items():
        window.vars[key].set(str(value))
    assert window.preview()

    def at(x: float, z: float) -> SimpleNamespace:
        cx, cy = window._to_canvas([(x, z)])
        return SimpleNamespace(x=cx, y=cy, delta=120)

    # wheel zoom keeps the point under the cursor; right-drag pans
    e = at(120, 20)
    window._wheel(e)
    assert window.view_zoom == pytest.approx(1.2)
    assert window._to_world(e.x, e.y) == pytest.approx((120, 20), abs=1e-6)
    before = window._to_world(100, 100)
    window._pan_start(SimpleNamespace(x=100, y=100))
    window._pan_move(SimpleNamespace(x=150, y=100))
    after = window._to_world(100, 100)
    assert before is not None and after is not None and after[0] > before[0]  # +x runs to the left
    window.fit_view()
    assert window.view_zoom == 1.0 and window.view_shift == (0.0, 0.0)

    m = window.machine
    fake = FakeGrbl(rx=1024)

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: m.can_move() and m.sender is not None and m.sender.planner_size == 100)
    m.jog_feed.set("600")

    def click(x: float, z: float) -> None:
        window._press(at(x, z))
        window._release(at(x, z))

    # a click does nothing until "Mouse moves the wire" is ticked
    click(100, 30)
    window.root.update()
    time.sleep(0.2)
    assert not any(line.startswith("$J=G90") for line in fake.lines)
    window.mouse_moves.set(True)

    def jog_to(x: float, z: float, letters: str) -> str:
        words = {"Z": x, "X": x, "A": z, "Y": z}
        return "$J=G90 G21 " + " ".join(f"{k}{words[k]:.3f}" for k in letters) + " F600"

    click(100, 30)  # both towers (axes 1-4 are Z A X Y)
    p = window.move_target
    assert p is not None
    pump(window, lambda: fake.lines[-1] == jog_to(p[0], p[1], "ZAXY"))
    assert p[0] == pytest.approx(100, abs=0.5) and p[1] == pytest.approx(30, abs=0.5)

    window.keys_down.add("x")  # X held: the X/Y tower only
    click(150, 40)
    window.keys_down.clear()
    p = window.move_target
    pump(window, lambda: fake.lines[-1] == jog_to(p[0], p[1], "XY"))
    assert p[2] == (1,)
    window.keys_down.add("z")
    assert window.towers_for_keys() == (0,)
    window.keys_down.clear()

    # drag: the towers follow, and the release sends the last point
    sent_before = len(fake.lines)
    window._press(at(100, 30))
    for i in range(1, 15):
        window._drag(at(100 + 5 * i, 30 + i))
        pump(window, lambda: True)
        time.sleep(0.03)
        window.root.update()
    window._release(at(170, 44))
    p = window.move_target
    pump(window, lambda: fake.lines[-1] == jog_to(p[0], p[1], "ZAXY"))
    follow = [line for line in fake.lines[sent_before:] if line.startswith("$J=G90")]
    assert 2 <= len(follow) < 15, follow  # several targets on the way, not one per mouse event
    assert m.sender is not None and m.sender.poll_seconds == 0.2  # back to normal after the drag



def test_max_rates_read_quietly_at_connect() -> None:
    fake = FakeGrbl(rx=128)
    s = connected(fake)
    try:
        end = time.monotonic() + 5
        while s.rapid_rate() is None and time.monotonic() < end:
            time.sleep(0.01)
        assert s.settings[110] == 3000 and s.settings[100] == 800
        assert s.rapid_rate() == 2500  # the slowest axis
        time.sleep(0.2)
        texts = [e.text for e in iter_events(s)]
        assert not any(t.startswith(("$110", "$$:")) or t == "$$" for t in texts)
        s.command("$$")  # asked by the operator: shown
        end = time.monotonic() + 5
        shown: list[str] = []
        while not any(t.startswith("$110=") for t in shown) and time.monotonic() < end:
            shown += [e.text for e in iter_events(s)]
            time.sleep(0.01)
        assert any(t.startswith("$110=") for t in shown)
    finally:
        s.close()


def test_section_speed_slider_reaches_rapids(window, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path
    from types import SimpleNamespace

    repo = Path(__file__).resolve().parents[2]
    values = {"root": repo / "examples/dxf/dryrun-root.dxf", "tip": repo / "examples/dxf/dryrun-tip.dxf",
              "profile": repo / "examples/profiles/real-machine.yaml", "span": "400", "stock_length": "220",
              "stock_height": "40", "out": tmp_path, "source": "dxf"}
    for key, value in values.items():
        window.vars[key].set(str(value))
    assert window.preview()
    m = window.machine
    assert "rapid: connect" in window.speed_text.get()
    fake = FakeGrbl(rx=1024)

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: m.can_move() and m.rapid_rate() == 2500)
    pump(window, lambda: int(float(window.speed_scale.cget("to"))) == 2500)  # slider top = rapid rate
    m.mouse_feed.set(2500)
    window._show_speed()
    assert window.speed_text.get().startswith("RAPID 2500")
    window.mouse_moves.set(True)
    cx, cy = window._to_canvas([(100, 30)])
    window._press(SimpleNamespace(x=cx, y=cy))
    window._release(SimpleNamespace(x=cx, y=cy))
    pump(window, lambda: fake.lines[-1].startswith("$J=G90") and fake.lines[-1].endswith(" F2500"))
    m.mouse_feed.set(400)
    window._show_speed()
    assert window.speed_text.get() == "400 mm/min (rapid 2500)"
    assert m.disconnect()
    import json

    assert json.loads((tmp_path / "machine.json").read_text())["mouse_feed"] == 400


# --- Kerf tab: cut, wait for heat off, pull out cold, step; measure and save ------------


def test_kerf_tab_runs_the_slots_and_saves_a_table(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import shutil
    from pathlib import Path

    from foamcam.profile import load_profile

    repo = Path(__file__).resolve().parents[2]
    profile = tmp_path / "machine.yaml"
    shutil.copy(repo / "examples/profiles/real-machine.yaml", profile)
    window.vars["profile"].set(str(profile))
    window.vars["speed"].set("150")
    m, k = window.machine, window.kerf
    assert str(k) in m.controls.tabs()

    fake = FakeGrbl(rx=1024, track={"X": 50.0, "Y": 40.0, "Z": 50.0, "A": 40.0})  # wire straight

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: m.can_move())
    m.sender.set_feed_override(150)
    pump(window, lambda: fake.ov == 150)

    asked: list[str] = []

    def confirm(title: str, text: str, **_k: object) -> bool:
        asked.append(title)
        return True

    monkeypatch.setattr("tkinter.messagebox.askokcancel", confirm)
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **kw: pytest.fail("wire reported bent"))

    k.target.set("150")
    k.count.set("3")
    k.generate()
    assert k.speeds_text.get() == "50, 95, 180"
    k.direction_label.set(k.dir_box.cget("values")[0])  # XZ+ : away from the operator
    k._offset_labels()
    k.depth.set("20")
    k.spacing.set("10")
    k.pullout.set("200")
    k.start()
    assert k.phase == "ready" and len(k.rows) == 3

    for i, speed in enumerate((50, 95, 180)):
        pump(window, lambda: k.phase == "ready" and k.cut_button.instate(["!disabled"]))
        k.cut()
        assert k.phase == "cutting"
        last = i == 2
        want = ("done" if last else "ready", i + 1)
        pump(window, lambda want=want: (k.phase, k.current) == want)
        cut = f"G1 Z20.000 X20.000 F{speed}"
        assert cut in fake.lines
        after = fake.lines[fake.lines.index(cut):]
        assert "G1 Z-20.000 X-20.000 F200" in after  # pulled out cold along the slot
        assert ("G1 A10.000 Y10.000 F200" in after) != last  # stepped up, except after the last slot
    assert fake.ov == 100  # the override was set back to 100 % for the cuts
    assert asked.count("Wire heat OFF") == 3  # the heat-off pause after every slot
    assert [s for s in k.states] == ["cut", "cut", "cut"]
    # back at the first slot's start, two spacings higher: each slot was pulled out to where it began
    assert fake.pos == {"X": 50.0, "Y": 60.0, "Z": 50.0, "A": 60.0}

    k.rows[0][0].set("1.80")
    k.rows[0][1].set("1.84")
    k.rows[1][0].set("1.2")
    k.rows[2][0].set("0.9")
    k.rows[2][1].set("1.0")
    assert k.rows[0][2].get() == "1.820"
    assert k.rows[2][2].get() == "0.950 !" and "Slot 3: faces differ" in k.warnings.get()
    k.table_name.set("eps-test")
    k.save()
    table = load_profile(profile).kerf_tables["eps-test"]
    assert [s for s, _ in table.points] == [50, 95, 180]
    assert [k for _, k in table.points] == pytest.approx([1.82, 1.2, 0.95])
    assert (tmp_path / "machine.yaml.bak").is_file()
    assert window.vars["kerf_table"].get() == "eps-test"
    assert m.disconnect()


def test_cut_tab_goes_to_the_start_then_cuts_across(window, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Machine tab, Cut tab: cold to the operator side at the height, then hot across (crosscut.py)."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    window.vars["profile"].set(str(repo / "examples/profiles/real-machine.yaml"))
    m, c = window.machine, window.cross_cut
    assert m.controls.tab(str(c), "text") == "Cut"
    c.height.set("35")
    c.speed.set("200")
    assert "Z=X=0 (operator side) to 300 at A=Y=35, 200 mm/min: 1.5 min" in c.note.get()
    c.height.set("400")
    assert "outside the vertical travel 0 to 280" in c.note.get()
    c.height.set("35")

    fake = FakeGrbl(rx=1024, track={"X": 120.0, "Y": 60.0, "Z": 120.0, "A": 60.0})

    def connect(device: str) -> Sender:
        s = Sender(fake, device)
        s.start()
        return s

    m._connect = connect
    m._ports = lambda: [("FAKE1", "fake grblHAL")]
    m.refresh_ports()
    m.connect()
    pump(window, lambda: m.can_move())
    asked: list[str] = []
    warned: list[str] = []

    def confirm(title: str, *_a: object, **_k: object) -> bool:
        asked.append(title)
        return True

    monkeypatch.setattr("tkinter.messagebox.askokcancel", confirm)
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, *a, **k: warned.append(title))

    c.cut()  # not at the start yet: refused
    assert warned == ["Not at the start"] and asked == []

    c.go_to_start()
    pump(window, lambda: m.progress_note.get().startswith("Finished: cross cut: wire to the start"))
    assert fake.lines[-2:] == ["G0 Z0.000 X0.000", "G0 A35.000 Y35.000"]  # across first, then down
    pump(window, lambda: m.work_position() == {"X": 0.0, "Y": 35.0, "Z": 0.0, "A": 35.0})

    c.cut()
    pump(window, lambda: m.progress_note.get().startswith("Finished: cross cut at z 35"))
    assert asked == ["Wire to the cut start", "Cut across"]
    assert fake.lines[-1] == "G1 Z300.000 X300.000 A35.000 Y35.000 F200"
    assert "Switch the wire heat off" in m.progress_note.get()
    assert m.settings["cross_z"] == "35" and m.settings["cross_speed"] == "200"
    assert m.disconnect()
