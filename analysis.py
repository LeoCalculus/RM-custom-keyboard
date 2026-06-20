import ctypes
import queue
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    import pyautogui
except ImportError:
    pyautogui = None
else:
    # The firmware already controls packet pacing; PyAutoGUI's default 100 ms
    # pause would make every mouse action noticeably late.
    pyautogui.PAUSE = 0
    # (0, 0) is a valid absolute coordinate in the controller protocol, so
    # PyAutoGUI's top-left-corner fail-safe cannot be enabled here.
    pyautogui.FAILSAFE = False


# PyAutoGUI sends virtual-key events. Some emulators only poll keyboard scan
# codes through DirectInput, so keyboard events use SendInput directly.
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeybdInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", _InputUnion),
    ]


try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
except AttributeError:
    _user32 = None
else:
    _user32.MapVirtualKeyW.argtypes = (ctypes.c_uint, ctypes.c_uint)
    _user32.MapVirtualKeyW.restype = ctypes.c_uint
    _user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_Input), ctypes.c_int)
    _user32.SendInput.restype = ctypes.c_uint


def send_scan_code(vk, key_up):
    """Inject a physical key event that DirectInput-based programs receive."""
    if _user32 is None:
        raise RuntimeError("Windows SendInput is unavailable")

    scan_code = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    if not scan_code:
        raise ValueError(f"No keyboard scan code for virtual key 0x{vk:02X}")

    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if key_up else 0)
    event = _Input(
        type=INPUT_KEYBOARD,
        ki=_KeybdInput(0, scan_code, flags, 0, 0),
    )
    if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


CRC8_INIT = 0xFF
CRC16_INIT = 0xFFFF
MAX_DATA_LENGTH = 512


def crc8(data, init=CRC8_INIT):
    crc = init
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
            crc &= 0xFF
    return crc


def crc16(data, init=CRC16_INIT):
    crc = init
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc


def hex_bytes(data):
    return " ".join(f"{value:02X}" for value in data)


def key_name(value):
    if value == 0:
        return "-"
    if 32 <= value <= 126:
        return f"'{chr(value)}'"
    return f"0x{value:02X}"


def decode_controller_payload(payload):
    """Decode the 8-byte custom keyboard/mouse payload used by command 0x0306."""
    if len(payload) != 8:
        return None

    key_value, x_word, y_word, reserved = struct.unpack("<HHHH", payload)
    return {
        "key_value": key_value,
        "key1": key_value & 0xFF,
        "key2": (key_value >> 8) & 0xFF,
        "x_position": x_word & 0x0FFF,
        "mouse_left": (x_word >> 12) & 0x0F,
        "y_position": y_word & 0x0FFF,
        "mouse_right": (y_word >> 12) & 0x0F,
        "reserved": reserved,
    }


class RefereeParser:
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        messages = []

        while True:
            sof_index = self.buf.find(0xA5)
            if sof_index < 0:
                if self.buf:
                    messages.append(("drop", bytes(self.buf)))
                    self.buf.clear()
                break

            if sof_index > 0:
                messages.append(("drop", bytes(self.buf[:sof_index])))
                del self.buf[:sof_index]

            if len(self.buf) < 5:
                break

            data_length = self.buf[1] | (self.buf[2] << 8)
            if data_length > MAX_DATA_LENGTH:
                messages.append(("bad_length", bytes(self.buf[:5]), data_length))
                del self.buf[0]
                continue

            frame_length = 5 + 2 + data_length + 2
            if len(self.buf) < frame_length:
                break

            frame = bytes(self.buf[:frame_length])
            if crc8(frame[:4]) != frame[4]:
                messages.append(("bad_crc8", frame[:5]))
                del self.buf[0]
                continue

            expected_crc16 = frame[-2] | (frame[-1] << 8)
            actual_crc16 = crc16(frame[:-2])
            if actual_crc16 != expected_crc16:
                messages.append(("bad_crc16", frame, actual_crc16, expected_crc16))
                del self.buf[0]
                continue

            cmd_id = frame[5] | (frame[6] << 8)
            payload = frame[7:7 + data_length]
            messages.append(("frame", {
                "seq": frame[3],
                "cmd_id": cmd_id,
                "data_length": data_length,
                "payload": payload,
                "crc16": expected_crc16,
                "frame": frame,
            }))
            del self.buf[:frame_length]

        return messages


def describe_frame(info):
    cmd_id = info["cmd_id"]
    payload = info["payload"]
    lines = [
        f"[{time.strftime('%H:%M:%S')}] OK seq={info['seq']} cmd=0x{cmd_id:04X} len={info['data_length']} crc16=0x{info['crc16']:04X}",
    ]

    if cmd_id == 0x0306:
        controller = decode_controller_payload(payload)
        if controller is None:
            lines.append(f"  0x0306 length error: expected 8, got {len(payload)}")
        else:
            lines.extend([
                "  0x0306 custom controller keyboard/mouse",
                f"  key_value=0x{controller['key_value']:04X} "
                f"key1={key_name(controller['key1'])} key2={key_name(controller['key2'])}",
                f"  mouse: x={controller['x_position']} y={controller['y_position']} "
                f"left={controller['mouse_left']} right={controller['mouse_right']}",
                f"  reserved=0x{controller['reserved']:04X}",
            ])
    else:
        lines.append(f"  data: {hex_bytes(payload)}")

    return "\n".join(lines) + "\n\n"


class PyAutoGuiInputController:
    """Apply custom-controller frames through Windows keyboard and mouse APIs."""

    MINIMUM_KEY_HOLD_MS = 50

    def __init__(self, scheduler):
        self.available = pyautogui is not None and _user32 is not None
        self.scheduler = scheduler
        self.enabled = False
        self.keys_down = set()
        self.key_down_at = {}
        self.pending_key_releases = {}
        self.left_down = False
        self.right_down = False

    @classmethod
    def vk_for_packet_key(cls, value):
        """Map the firmware's byte-sized ASCII key value to a Windows VK code."""
        if ord("a") <= value <= ord("z"):
            return ord(chr(value).upper())
        if ord("A") <= value <= ord("Z"):
            return value
        if ord("0") <= value <= ord("9"):
            return value

        special_keys = {
            0x08: 0x08,  # VK_BACK
            0x09: 0x09,  # VK_TAB
            0x0D: 0x0D,  # VK_RETURN
            0x1B: 0x1B,  # VK_ESCAPE
            0x20: 0x20,  # VK_SPACE
        }
        return special_keys.get(value)

    def set_enabled(self, enabled):
        if not self.available:
            return False

        enabled = bool(enabled)
        if not enabled:
            self.release_all()
        self.enabled = enabled
        return True

    def _key_event(self, vk, key_up):
        send_scan_code(vk, key_up)

    def _cancel_pending_key_release(self, vk):
        callback_id = self.pending_key_releases.pop(vk, None)
        if callback_id is not None:
            self.scheduler.after_cancel(callback_id)

    def _release_key_now(self, vk):
        self.pending_key_releases.pop(vk, None)
        if vk not in self.keys_down:
            return
        self._key_event(vk, key_up=True)
        self.keys_down.remove(vk)
        self.key_down_at.pop(vk, None)

    def _release_key(self, vk):
        """Release now, or hold long enough for a polling emulator to see it."""
        if vk not in self.keys_down or vk in self.pending_key_releases:
            return False

        elapsed_ms = (time.monotonic() - self.key_down_at[vk]) * 1000
        remaining_ms = self.MINIMUM_KEY_HOLD_MS - elapsed_ms
        if remaining_ms <= 0:
            self._release_key_now(vk)
            return True

        callback_id = self.scheduler.after(
            max(1, round(remaining_ms)),
            lambda key=vk: self._release_key_now(key),
        )
        self.pending_key_releases[vk] = callback_id
        return False

    def _mouse_event(self, button, button_up):
        if button_up:
            pyautogui.mouseUp(button=button)
        else:
            pyautogui.mouseDown(button=button)

    def release_all(self):
        if not self.available:
            return

        if self.left_down:
            self._mouse_event("left", button_up=True)
            self.left_down = False
        if self.right_down:
            self._mouse_event("right", button_up=True)
            self.right_down = False

        for vk in tuple(self.keys_down):
            self._cancel_pending_key_release(vk)
            self._key_event(vk, key_up=True)
        self.keys_down.clear()
        self.key_down_at.clear()

    def apply(self, controller):
        """Synchronize Windows input state with one decoded controller packet."""
        if not self.enabled or not self.available:
            return None

        desired_keys = set()
        unsupported = []
        for value in (controller["key1"], controller["key2"]):
            if value == 0:
                continue
            key = self.vk_for_packet_key(value)
            if key is None:
                unsupported.append(key_name(value))
            else:
                desired_keys.add(key)

        changes = []
        for key in self.keys_down - desired_keys:
            if self._release_key(key):
                changes.append(f"key up {key_name(key)}")
            else:
                changes.append(f"key up {key_name(key)} (delayed)")
        for key in desired_keys - self.keys_down:
            self._cancel_pending_key_release(key)
            self._key_event(key, key_up=False)
            self.keys_down.add(key)
            self.key_down_at[key] = time.monotonic()
            changes.append(f"key down {key_name(key)}")
        for key in desired_keys & self.keys_down:
            self._cancel_pending_key_release(key)

        x = controller["x_position"]
        y = controller["y_position"]
        # Coordinates are absolute screen pixels. (0, 0) is valid and moves the
        # pointer to the top-left corner, as represented by the protocol.
        pyautogui.moveTo(x, y, duration=0)
        changes.append(f"mouse move ({x}, {y})")

        desired_left = bool(controller["mouse_left"])
        desired_right = bool(controller["mouse_right"])

        if desired_left != self.left_down:
            self._mouse_event("left", button_up=not desired_left)
            self.left_down = desired_left
            changes.append("left down" if desired_left else "left up")

        if desired_right != self.right_down:
            self._mouse_event("right", button_up=not desired_right)
            self.right_down = desired_right
            changes.append("right down" if desired_right else "right up")

        if unsupported:
            changes.append("unsupported key " + ", ".join(unsupported))

        return "; ".join(changes) if changes else "state unchanged"


class SerialWorker(threading.Thread):
    def __init__(self, port, baudrate, out_queue):
        super().__init__(daemon=True)
        self.port = port
        self.baudrate = baudrate
        self.out_queue = out_queue
        self.stop_event = threading.Event()
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self.out_queue.put(("status", f"Opened {self.port} @ {self.baudrate} 8N1"))

            while not self.stop_event.is_set():
                waiting = self.ser.in_waiting
                data = self.ser.read(waiting or 1)
                if data:
                    self.out_queue.put(("data", data))
        except Exception as exc:
            self.out_queue.put(("error", str(exc)))
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.out_queue.put(("closed", None))

    def stop(self):
        self.stop_event.set()


class AnalyzerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RoboMaster Serial Analyzer")
        self.geometry("1100x700")
        self.minsize(900, 520)

        self.parser = RefereeParser()
        self.events = queue.Queue()
        self.worker = None
        self.input_controller = PyAutoGuiInputController(self)

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.status_var = tk.StringVar(value="Idle")
        self.pc_control_var = tk.BooleanVar(value=False)

        self._build_ui()
        self.refresh_ports()
        self.after(40, self.poll_events)

        if serial is None:
            messagebox.showerror(
                "pyserial missing",
                "pyserial is not installed. Run: python -m pip install pyserial",
            )

    def _build_ui(self):
        controls = ttk.Frame(self, padding=8)
        controls.pack(fill=tk.X)

        ttk.Label(controls, text="Port").pack(side=tk.LEFT)
        self.port_combo = ttk.Combobox(controls, textvariable=self.port_var, width=28, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=(6, 8))

        ttk.Button(controls, text="Refresh", command=self.refresh_ports).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(controls, text="Baud").pack(side=tk.LEFT)
        ttk.Entry(controls, textvariable=self.baud_var, width=10).pack(side=tk.LEFT, padx=(6, 12))

        self.start_button = ttk.Button(controls, text="Start", command=self.toggle_serial)
        self.start_button.pack(side=tk.LEFT)

        ttk.Button(controls, text="Clear", command=self.clear_views).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(controls, text="Inject Sample", command=self.inject_sample).pack(side=tk.LEFT, padx=(8, 0))

        self.pc_control_button = ttk.Checkbutton(
            controls,
            text="Apply to PC",
            variable=self.pc_control_var,
            command=self.toggle_pc_control,
        )
        self.pc_control_button.pack(side=tk.LEFT, padx=(16, 0))
        self.release_button = ttk.Button(
            controls,
            text="Release Inputs",
            command=self.release_pc_inputs,
        )
        self.release_button.pack(side=tk.LEFT, padx=(6, 0))

        if not self.input_controller.available:
            self.pc_control_button.configure(state=tk.DISABLED)
            self.release_button.configure(state=tk.DISABLED)

        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.RIGHT)

        panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        raw_frame = ttk.Labelframe(panes, text="Raw Hex Bytes")
        parsed_frame = ttk.Labelframe(panes, text="Parsed Frames")
        panes.add(raw_frame, weight=1)
        panes.add(parsed_frame, weight=1)

        self.raw_text = scrolledtext.ScrolledText(raw_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.raw_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.parsed_text = scrolledtext.ScrolledText(parsed_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.parsed_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    def refresh_ports(self):
        if list_ports is None:
            self.port_combo["values"] = []
            self.status_var.set("pyserial missing")
            return

        ports = [f"{port.device}  {port.description}" for port in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
        self.status_var.set(f"{len(ports)} port(s)")

    def selected_port(self):
        text = self.port_var.get().strip()
        return text.split()[0] if text else ""

    def toggle_serial(self):
        if self.worker:
            self.input_controller.release_all()
            self.worker.stop()
            self.status_var.set("Stopping...")
            self.start_button.configure(state=tk.DISABLED)
            return

        if serial is None:
            messagebox.showerror("pyserial missing", "Run: python -m pip install pyserial")
            return

        port = self.selected_port()
        if not port:
            messagebox.showwarning("No port", "Select a COM port first.")
            return

        try:
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showwarning("Bad baud", "Baud rate must be an integer.")
            return

        self.parser = RefereeParser()
        self.worker = SerialWorker(port, baud, self.events)
        self.worker.start()
        self.start_button.configure(text="Stop")
        self.status_var.set("Opening...")

    def toggle_pc_control(self):
        if not self.pc_control_var.get():
            self.input_controller.set_enabled(False)
            self.status_var.set("PC control disabled")
            return

        if not self.input_controller.available:
            self.pc_control_var.set(False)
            messagebox.showerror(
                "PC input unavailable",
                "This feature requires Windows and pyautogui. Install it with: "
                "python -m pip install pyautogui",
            )
            return

        accepted = messagebox.askyesno(
            "Apply serial input to PC",
            "Valid 0x0306 frames will now control the actual keyboard and mouse "
            "of the current foreground application. Continue?",
            icon=messagebox.WARNING,
        )
        if not accepted:
            self.pc_control_var.set(False)
            return

        self.input_controller.set_enabled(True)
        self.status_var.set("PC control enabled")

    def release_pc_inputs(self):
        self.input_controller.release_all()
        self.input_controller.set_enabled(False)
        self.pc_control_var.set(False)
        self.status_var.set("PC inputs released")

    def clear_views(self):
        self.raw_text.delete("1.0", tk.END)
        self.parsed_text.delete("1.0", tk.END)
        self.parser = RefereeParser()

    def inject_sample(self):
        payload = struct.pack("<HHHH", ord("O"), 1160, 560, 0)
        press_frame = self.build_frame(0x0306, payload, seq=0)
        release_payload = struct.pack("<HHHH", 0, 0, 0, 0)
        release_frame = self.build_frame(0x0306, release_payload, seq=1)
        self.events.put(("data", press_frame + release_frame))

    def build_frame(self, cmd_id, payload, seq=0):
        header_without_crc = struct.pack("<BHB", 0xA5, len(payload), seq & 0xFF)
        header = header_without_crc + bytes([crc8(header_without_crc)])
        body = struct.pack("<H", cmd_id) + payload
        without_tail = header + body
        return without_tail + struct.pack("<H", crc16(without_tail))

    def poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "data":
                    self.append_raw(payload)
                    for message in self.parser.feed(payload):
                        self.append_parsed(message)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "error":
                    self.status_var.set("Error")
                    self.append_parsed(("error", payload))
                elif kind == "closed":
                    self.input_controller.release_all()
                    self.worker = None
                    self.start_button.configure(text="Start", state=tk.NORMAL)
                    if self.status_var.get() == "Stopping...":
                        self.status_var.set("Closed")
        except queue.Empty:
            pass

        self.after(40, self.poll_events)

    def append_raw(self, data):
        self.raw_text.insert(tk.END, hex_bytes(data) + "\n")
        self.raw_text.see(tk.END)
        self.trim_text(self.raw_text)

    def append_parsed(self, message):
        kind = message[0]
        if kind == "frame":
            text = describe_frame(message[1])
            pc_action = self.apply_frame_to_pc(message[1])
            if pc_action:
                text = text.rstrip() + f"\n  PC action: {pc_action}\n\n"
        elif kind == "drop":
            text = f"[{time.strftime('%H:%M:%S')}] DROP noise: {hex_bytes(message[1])}\n\n"
        elif kind == "bad_length":
            text = f"[{time.strftime('%H:%M:%S')}] BAD length={message[2]} header={hex_bytes(message[1])}\n\n"
        elif kind == "bad_crc8":
            text = f"[{time.strftime('%H:%M:%S')}] BAD CRC8 header={hex_bytes(message[1])}\n\n"
        elif kind == "bad_crc16":
            text = (
                f"[{time.strftime('%H:%M:%S')}] BAD CRC16 "
                f"actual=0x{message[2]:04X} expected=0x{message[3]:04X} "
                f"frame={hex_bytes(message[1])}\n\n"
            )
        elif kind == "error":
            text = f"[{time.strftime('%H:%M:%S')}] SERIAL ERROR: {message[1]}\n\n"
        else:
            text = f"[{time.strftime('%H:%M:%S')}] {message}\n\n"

        self.parsed_text.insert(tk.END, text)
        self.parsed_text.see(tk.END)
        self.trim_text(self.parsed_text)

    def apply_frame_to_pc(self, info):
        if info["cmd_id"] != 0x0306:
            return None

        controller = decode_controller_payload(info["payload"])
        if controller is None:
            return None

        try:
            return self.input_controller.apply(controller)
        except Exception as exc:
            self.input_controller.enabled = False
            self.pc_control_var.set(False)
            return f"PC input error; PC control disabled: {exc}"

    def trim_text(self, widget, max_lines=5000):
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > max_lines:
            widget.delete("1.0", f"{line_count - max_lines}.0")

    def destroy(self):
        self.input_controller.release_all()
        if self.worker:
            self.worker.stop()
        super().destroy()


if __name__ == "__main__":
    app = AnalyzerApp()
    app.mainloop()
