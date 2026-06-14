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
        if len(payload) != 8:
            lines.append(f"  0x0306 length error: expected 8, got {len(payload)}")
        else:
            key_value, x_word, y_word, reserved = struct.unpack("<HHHH", payload)
            key1 = key_value & 0xFF
            key2 = (key_value >> 8) & 0xFF
            x_position = x_word & 0x0FFF
            mouse_left = (x_word >> 12) & 0x0F
            y_position = y_word & 0x0FFF
            mouse_right = (y_word >> 12) & 0x0F

            lines.extend([
                "  0x0306 custom controller keyboard/mouse",
                f"  key_value=0x{key_value:04X} key1={key_name(key1)} key2={key_name(key2)}",
                f"  mouse: x={x_position} y={y_position} left={mouse_left} right={mouse_right}",
                f"  reserved=0x{reserved:04X}",
            ])
    else:
        lines.append(f"  data: {hex_bytes(payload)}")

    return "\n".join(lines) + "\n\n"


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

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.status_var = tk.StringVar(value="Idle")

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

    def clear_views(self):
        self.raw_text.delete("1.0", tk.END)
        self.parsed_text.delete("1.0", tk.END)
        self.parser = RefereeParser()

    def inject_sample(self):
        payload = struct.pack("<HHHH", ord("O"), 1160, 560, 0)
        frame = self.build_frame(0x0306, payload, seq=0)
        self.events.put(("data", frame))

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

    def trim_text(self, widget, max_lines=5000):
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > max_lines:
            widget.delete("1.0", f"{line_count - max_lines}.0")

    def destroy(self):
        if self.worker:
            self.worker.stop()
        super().destroy()


if __name__ == "__main__":
    app = AnalyzerApp()
    app.mainloop()
