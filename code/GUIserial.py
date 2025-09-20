from tkinter import *
from tkinter import ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import numpy as np
import threading, queue, time
from collections import deque
import serial 

# ----------------------------- Utilities -----------------------------
def _from_rgb(rgb):
    r, g, b = rgb
    return f'#{r:02x}{g:02x}{b:02x}'


class SerialIO(threading.Thread):
    """
    Thread that handles both TX (commands) and RX (frames).
    - in_q:  commands (strings) from GUI to device
    - out_q: parsed frames to GUI: ('line', y, idx, dir) or ('point', y)
    """
    def __init__(self, port, baud, in_q: queue.Queue, out_q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.in_q = in_q
        self.out_q = out_q
        self.stop = stop_event
        self.ser = None
        self.buf = b""

    def _open(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)

    def _readline(self):
        """
        Return one line (bytes) without trailing \r or \n; None if not yet complete.
        Handles CRLF and multiple lines per read.
        """
        # fast path: already have a newline in buffer
        for sep in (b"\r\n", b"\n"):
            idx = self.buf.find(sep)
            if idx >= 0:
                line = self.buf[:idx]
                self.buf = self.buf[idx+len(sep):]
                return line

        # read more
        chunk = self.ser.read(1024)
        if chunk:
            self.buf += chunk
            # try again
            for sep in (b"\r\n", b"\n"):
                idx = self.buf.find(sep)
                if idx >= 0:
                    line = self.buf[:idx]
                    self.buf = self.buf[idx+len(sep):]
                    return line
        return None

    @staticmethod
    def _parse_header(hline: bytes):
        """
        Parse header like:
          b'LINE OK N=256 IDX=0 DIR=+1'
          b'POINT OK COUNT=200'
          b'OK MSG="..."'
          b'ERR CODE=... MSG="..."'
        Returns a dict: {'kind': 'line'/'point'/'ok'/'err', ...}
        """
        try:
            txt = hline.decode("utf-8", errors="replace").strip()
            parts = txt.split()
            if not parts:
                return None
            head = parts[0].upper()
            kv = {}
            for p in parts[1:]:
                if '=' in p:
                    k, v = p.split('=', 1)
                    kv[k.upper()] = v
            if head == "LINE" and parts[1].upper() == "OK":
                return {"kind": "line",
                        "N": int(kv.get("N", "0")),
                        "IDX": int(kv.get("IDX", "0")),
                        "DIR": int(kv.get("DIR", "+1"))}
            elif head == "POINT" and parts[1].upper() == "OK":
                return {"kind": "point",
                        "COUNT": int(kv.get("COUNT", "0"))}
            elif head == "OK":
                return {"kind": "ok", "raw": txt}
            elif head == "ERR":
                return {"kind": "err", "raw": txt}
            else:
                return {"kind": "unknown", "raw": txt}
        except Exception:
            return None

    @staticmethod
    def _parse_csv_floats(line: bytes, expected: int | None):
        try:
            arr = [float(t) for t in line.decode("utf-8").strip().split(",") if t]
            if expected is not None and len(arr) != expected:
                # allow mismatch but still return whatever we got
                pass
            return np.array(arr, dtype=float)
        except Exception:
            return None

    def run(self):
        self._open()
        try:
            while not self.stop.is_set():
                # 1) writes: send all pending commands
                try:
                    while True:
                        cmd = self.in_q.get_nowait()
                        if not cmd.endswith("\n"):
                            cmd += "\n"
                        self.ser.write(cmd.encode("utf-8"))
                        print(f"SENT CMD: {cmd.strip()}")
                except queue.Empty:
                    pass

                # 2) reads: try to get a header line
                h = self._readline()
                print(f"GOT LINE: {h}")
                if h is None:
                    continue

                hdr = self._parse_header(h)
                if not hdr:
                    continue

                if hdr["kind"] == "line":
                    # read one CSV payload line
                    pl = self._readline()
                    if pl is None:
                        continue
                    y = self._parse_csv_floats(pl, hdr["N"])
                    if y is None:
                        continue
                    self.out_q.put(("line", y, hdr["IDX"], hdr["DIR"]))

                elif hdr["kind"] == "point":
                    pl = self._readline()
                    if pl is None:
                        continue
                    y = self._parse_csv_floats(pl, hdr["COUNT"])
                    if y is None:
                        continue
                    self.out_q.put(("point", y))

                else:
                    # OK/ERR/unknown → you can log or ignore
                    # print("STATUS:", hdr.get("raw"))
                    pass

        finally:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass



# ----------------------------- Simulated Serial -----------------------------
class SimSerial(threading.Thread):
    """
    Simulated microscope "serial" device. Listens for commands on cmd_q and
    posts frames to out_q. When idle, emits 'point' frames for stability tuning.
    """
    def __init__(self, cmd_q: queue.Queue, out_q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.cmd_q = cmd_q
        self.out_q = out_q
        self.stop = stop_event
        self.t = 0.0  # phase for stable demo signal

    def _make_line(self, n, line_idx, direction):
        # Make a wavy line with slow drift + noise; values ~ [-6, +6]
        x = np.linspace(0, 1, n)
        base = 2.0 * np.sin(2*np.pi*(x*3 + 0.03*line_idx) + 0.4*self.t)
        drift = 0.4*np.sin(0.2*line_idx + 0.1*self.t)
        noise = np.random.normal(0.0, 0.25, size=n)
        y = base + drift + noise
        # Zig-zag travel: device chooses direction and sends it back
        if direction < 0:
            y = y[::-1]
        return y.astype(np.float32)

    def _make_point(self, n=200):
        # Stability mode: single-point stream
        x = np.linspace(0, 1, n)
        y = 0.3*np.sin(2*np.pi*(5*x + 0.1*self.t)) + np.random.normal(0.0, 0.05, size=n)
        return y.astype(np.float32)

    def run(self):
        next_idle_emit = time.time()
        while not self.stop.is_set():
            try:
                cmd = self.cmd_q.get(timeout=0.02)
            except queue.Empty:
                # idle: emit a small "point" packet sometimes so GUI keeps updating
                if time.time() >= next_idle_emit:
                    y = self._make_point(200)
                    self.out_q.put(("point", y))
                    next_idle_emit = time.time() + 0.15
                    self.t += 0.05
                continue

            if cmd["cmd"] == "line":
                n = int(cmd["linear_size"])
                idx = int(cmd["line_idx"])
                direction = int(cmd["dir"])  # +1 forward, -1 backward
                # emulate scan time
                time.sleep(min(0.002*n, 0.3))
                y = self._make_line(n, idx, direction)
                self.out_q.put(("line", y, idx, direction))
                self.t += 0.02

            elif cmd["cmd"] == "point":
                y = self._make_point(200)
                self.out_q.put(("point", y))
                self.t += 0.02


# ----------------------------- Main App -----------------------------
class STMApp:
    def __init__(self, root):
        self.m = root
        self.m.title("Scanning Tunneling Microscope Control")
        self.m.geometry("1400x800")

        # Queues for device I/O
        #self.cmd_q = queue.Queue()      # GUI -> device
        #self.data_q = queue.Queue()     # device -> GUI
        #self.stop_ev = threading.Event()
        self.tx_q   = queue.Queue()   # GUI -> device (string commands)
        self.data_q = queue.Queue()   # device -> GUI (parsed frames)
        self.stop_ev = threading.Event()

        # --- Serial config: set your Pico port ---
        PORT = "COM8"            # Windows example; on Linux: "/dev/ttyACM0" or "/dev/ttyUSB0"
        BAUD = 115200

        # Start real serial I/O
        self.serial = SerialIO(PORT, BAUD, self.tx_q, self.data_q, self.stop_ev)
        self.serial.start()

        # Start polling
        self._last_point_req = 0.0
        self.m.after(30, self._poll_device)

        # On close
        self.m.protocol("WM_DELETE_WINDOW", self._on_close)


        # State
        self.display_duration = 10.0
        self.linear_size = 128          # will be overwritten by update_scan_label
        self.scanning = False
        self.line_idx = 0
        self.direction = +1             # +1 left->right, -1 right->left (device tells us; GUI mirrors it)
        self.y_buffer = deque(maxlen=10000)  # rolling time-series buffer for ax2

        # ----------------- LAYOUT -----------------------
        self.side_bar = Frame(self.m, bg='lightgrey', relief='sunken', borderwidth=2)
        self.side_bar.pack(expand=False, fill='y', side='left', anchor='nw')

        self.main_area = Frame(self.m, bg='white')
        self.main_area.pack(expand=True, fill='both', side='right')

        # ----------------- FIGURE + AXES -----------------
        self.fig = Figure(figsize=(5, 6), dpi=100)
        gs = self.fig.add_gridspec(2, 1, height_ratios=[4, 1])
        self.ax1 = self.fig.add_subplot(gs[0], adjustable='box', aspect='equal')
        self.ax2 = self.fig.add_subplot(gs[1])
        self.ax2_hist = self.ax2.twiny()  # independent horizontal axis for histogram

        # Top image: start blank; filled at scan start
        self.topo = None
        self.im = None  # created in reset_topography()

        # Bottom plot setup
        self.ax2.grid()
        self.ax2.set_ylim(-12, 12)
        self.ax2.set_xlim(-self.display_duration, 0)
        (self.line2,) = self.ax2.plot([], [], label="y vs time")  # data set later
        self.update_histogram(np.array([]), norm_max=5)

        self.fig.tight_layout(pad=0.0)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.main_area)
        self.canvas.draw()
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.main_area)
        self.toolbar.update()
        self.canvas.get_tk_widget().pack(side=TOP, fill=BOTH, expand=1)

        # ----------------- CONTROLS (same layout/packing) -----------------
        Label(self.side_bar, text="Bias Voltage [V]", bg='lightgrey', font=("Arial", 12)).pack()
        self.bias_slider = Scale(self.side_bar, from_=-3, to=0, orient=HORIZONTAL,
                                 length=400, bg='lightgrey', resolution=0.01,
                                 command=self.on_bias_change)
        self.bias_slider.pack(padx=10, pady=5)

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        Label(self.side_bar, text="Vertical Stepper Control", bg='lightgrey',
              font=("Arial", 12)).pack(pady=5)
        button_frame = Frame(self.side_bar, bg='lightgrey')
        button_frame.pack(pady=5)
        Button(button_frame, text="Step Up", width=10,
               command=lambda: self.on_step('step_up')).grid(row=0, column=0, padx=20, pady=2)
        Button(button_frame, text="Jog Up", width=10,
               command=lambda: self.on_step('jog_up')).grid(row=0, column=1, padx=20, pady=2)
        Button(button_frame, text="Step Down", width=10,
               command=lambda: self.on_step('step_down')).grid(row=1, column=0, padx=20, pady=2)
        Button(button_frame, text="Jog Down", width=10,
               command=lambda: self.on_step('jog_down')).grid(row=1, column=1, padx=20, pady=2)

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        Label(self.side_bar, text="Set Point [V]", bg='lightgrey', font=("Arial", 12)).pack(pady=5)
        self.setpoint_slider = Scale(self.side_bar, from_=0, to=5, orient=HORIZONTAL,
                                     length=400, bg='lightgrey', resolution=0.01,
                                     command=self.on_setpoint_change)
        self.setpoint_slider.pack(padx=10, pady=5)

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        Label(self.side_bar, text="Down-sampling multiplier", bg='lightgrey',
              font=("Arial", 12)).pack(pady=5)
        self.resolution_var = IntVar(value=128)
        res_frame = Frame(self.side_bar, bg='lightgrey')
        res_frame.pack(pady=5)
        for v in [1, 2, 4, 8, 16, 32, 64, 128, 256]:
            Radiobutton(res_frame, text=f"{v}x", variable=self.resolution_var, value=v,
                        bg='lightgrey', command=self.on_resolution_change).pack(side='left')

        Label(self.side_bar, text="Zoom", bg='lightgrey', font=("Arial", 12)).pack(pady=5)
        self.zoom_var = IntVar(value=1)
        zoom_frame = Frame(self.side_bar, bg='lightgrey')
        zoom_frame.pack(pady=5)
        for v in [1, 2, 4, 8, 16, 32, 64, 128, 256]:
            Radiobutton(zoom_frame, text=f"{v}x", variable=self.zoom_var, value=v,
                        bg='lightgrey', command=self.on_zoom_change).pack(side='left')

        self.scan_label = Label(self.side_bar, text="Scan Size: ", bg='lightgrey', font=("Arial", 12))
        self.scan_label.pack(pady=5)
        self.time_label = Label(self.side_bar, text="Estimated Scan Time: ", bg='lightgrey', font=("Arial", 12))
        self.time_label.pack(pady=5)
        self.update_scan_label()

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        Label(self.side_bar, text="Time Scale [s]", bg='lightgrey', font=("Arial", 12)).pack(pady=5)
        self.time_scale = Scale(self.side_bar, from_=1, to=60, length=400, orient='horizontal',
                                bg='lightgrey', font=("Arial", 12), command=self.on_time_scale_drag)
        self.time_scale.pack(pady=5)
        self.time_scale.set(10)
        self.time_scale.bind("<ButtonRelease-1>", lambda e: self.on_time_scale_change())

        self.zstab_label = Label(self.side_bar, text="Z-Stability: ", bg='lightgrey', font=("Arial", 12))
        self.zstab_label.pack(pady=5)
        self.update_zstab_label(0.0)

        self.progress_bar = ttk.Progressbar(self.side_bar, orient='horizontal', length=400, mode='determinate')
        self.progress_bar.pack(padx=10, pady=5, side='bottom')

        self.start_button = Button(self.side_bar, text="Start Scan", bg='green', fg='white',
                                   font=("Arial", 16), command=self.toggle_scan)
        self.start_button.pack(pady=5, ipadx=10, ipady=5, side='bottom')

        # ---------- Threads & polling ----------
        #self.sim = SimSerial(self.cmd_q, self.data_q, self.stop_ev)
        #self.sim.start()
        #self.m.after(30, self._poll_device)   # GUI polling

        # Clean exit
        self.m.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------- Plot helpers -----------------
    def reset_topography(self, N):
        # Blank image (NaNs): show nothing until lines arrive
        self.topo = np.full((N, N), np.nan, dtype=np.float32)
        self.ax1.clear()
        self.ax1.set_aspect('equal', adjustable='box')
        # Use imshow: faster to update than pcolormesh; NaNs can be made transparent
        #cmap = self.fig.colormaps.get_cmap('viridis').copy()
        #cmap.set_bad(alpha=0.0)
        self.im = self.ax1.imshow(self.topo, origin='lower', interpolation='nearest', cmap='viridis', vmin=-6, vmax=6)
        self.fig.tight_layout(pad=0.0)

    def update_topography_line(self, y, line_idx, direction):
        N = self.topo.shape[0]
        if len(y) != N:
            # Resize received line to current N if device differs slightly
            y = np.interp(np.linspace(0, len(y)-1, N), np.arange(len(y)), y)
        row = line_idx
        if direction >= 0:
            self.topo[row, :] = y
        else:
            self.topo[row, :] = y[::-1]
        self.im.set_data(self.topo)

    def append_time_series(self, y):
        # Append to rolling buffer and update ax2
        self.y_buffer.extend(y.tolist())
        n = len(self.y_buffer)
        if n == 0:
            self.line2.set_data([], [])
            return
        # Build x as a linear time base over the selected window
        # We display last K points (sample-based). K proportional to duration.
        K = min(n, int(200*self.display_duration))  # ~200 Hz visual density
        yv = np.array(list(self.y_buffer)[-K:], dtype=float)
        xv = np.linspace(-self.display_duration, 0, K)
        self.line2.set_data(xv, yv)
        self.ax2.set_xlim(-self.display_duration, 0)
        # (keep fixed y-limits you set earlier)
        self.update_histogram(yv, norm_max=5)
        self.update_zstab_label(float(np.std(yv)))

    def update_histogram(self, y, norm_max=5):
        self.ax2_hist.cla()
        if y is None or len(y) == 0:
            self.ax2_hist.set_xlim(0, norm_max)
            self.ax2_hist.xaxis.set_visible(False)
            self.ax2_hist.set_ylim(self.ax2.get_ylim())
            return
        counts, bins, patches = self.ax2_hist.hist(
            y, bins=20, orientation="horizontal", alpha=0.7, color="tab:red"
        )
        max_count = max(counts.max(), 1.0)
        scale = float(norm_max) / max_count
        for rect in patches:
            rect.set_width(rect.get_width() * scale)
        self.ax2_hist.set_xlim(0, norm_max)
        self.ax2_hist.xaxis.set_visible(False)
        self.ax2_hist.set_ylim(self.ax2.get_ylim())

    def redraw(self, tight=False):
        if tight:
            self.fig.tight_layout(pad=0.0)
        self.canvas.draw_idle()

    # ----------------- UI callbacks -----------------
    def on_bias_change(self, val):
        print(f"[CB] Bias -> {float(val):.2f} V")

    def on_setpoint_change(self, val):
        print(f"[CB] Setpoint -> {float(val):.2f} V")

    def on_step(self, action):
        print(f"[CB] Stepper -> {action}")

    def on_resolution_change(self):
        self.update_scan_label()

    def on_zoom_change(self):
        self.update_scan_label()

    def on_time_scale_drag(self, val):
        self.display_duration = float(val)
        self.ax2.set_xlim(-self.display_duration, 0)
        self.canvas.draw_idle()

    def on_time_scale_change(self):
        self.display_duration = float(self.time_scale.get())
        self.redraw()

    '''def toggle_scan(self):
        if not self.scanning:
            # START
            self.scanning = True
            self.start_button.config(text='Stop Scan', bg='red')
            self.progress_bar['value'] = 0
            self.line_idx = 0
            self.direction = +1
            self.reset_topography(self.linear_size)
            self.y_buffer.clear()
            self._request_next_line()
        else:
            # STOP
            self.scanning = False
            self.start_button.config(text='Start Scan', bg='green')
            self.progress_bar.stop()
            self.progress_bar['value'] = 0'''
    def toggle_scan(self):
        if not self.scanning:
            self.scanning = True
            self.start_button.config(text='Stop Scan', bg='red')
            self.progress_bar['value'] = 0
            self.line_idx = 0
            self.direction = +1
            self.reset_topography(self.linear_size)
            self.y_buffer.clear()
            self._request_start()
            self._request_next_line()
        else:
            self.scanning = False
            self.start_button.config(text='Start Scan', bg='green')
            self.progress_bar.stop()
            self.progress_bar['value'] = 0

    # ----------------- Status/labels -----------------
    def update_scan_label(self):
        zoom = int(self.zoom_var.get())
        res = int(self.resolution_var.get())
        self.linear_size = max(1, 65536 // (zoom * res))
        self.scan_label.config(text=f"Scan Size: {self.linear_size} x {self.linear_size}")

        time_per_pixel_ms = 0.01  # ms
        total_seconds = int(((self.linear_size ** 2) * time_per_pixel_ms) // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        self.time_label.config(text=f"Estimated Scan Time: {minutes} min {seconds} s")
        self.redraw()

    def update_zstab_label(self, std):
        self.zstab_label.config(text=f"Z-Stability: {std:.2f}")
        self.zstab_label.config(fg='green' if std < 0.5 else 'red')

    # ----------------- Device polling & requests -----------------
    #def _poll_device(self):
        """Poll frames from device and update plots (runs on Tk thread via .after)."""
        '''last_line = None
        last_point = None
        try:
            while True:
                frame = self.data_q.get_nowait()
                kind = frame[0]
                if kind == "line":
                    _, y, idx, direction = frame
                    last_line = (y, idx, direction)
                elif kind == "point":
                    _, y = frame
                    last_point = y
        except queue.Empty:
            pass

        if last_line is not None:
            y, idx, direction = last_line
            if self.topo is not None and 0 <= idx < self.topo.shape[0]:
                self.update_topography_line(y, idx, direction)
            self.append_time_series(y)
            self.progress_bar['value'] = 100.0 * (idx + 1) / self.linear_size
            self.redraw()
            if self.scanning:
                if idx + 1 < self.linear_size:
                    # Device chooses direction (zig-zag): flip each line request
                    self.direction = -self.direction
                    self.line_idx = idx + 1
                    self._request_next_line()
                else:
                    # Finished
                    self.toggle_scan()

        elif last_point is not None and not self.scanning:
            # Stability mode updates when idle
            self.append_time_series(last_point)
            self.redraw()

        # schedule next poll
        self.m.after(30, self._poll_device)'''

    def _poll_device(self):
        """Poll parsed frames from serial and update plots."""
        last_line = None
        last_point = None
        try:
            while True:
                frame = self.data_q.get_nowait()
                kind = frame[0]
                if kind == "line":
                    _, y, idx, direction = frame
                    last_line = (y, idx, direction)
                elif kind == "point":
                    _, y = frame
                    last_point = y
        except queue.Empty:
            pass

        if last_line is not None:
            y, idx, direction = last_line
            if self.topo is not None and 0 <= idx < self.topo.shape[0]:
                self.update_topography_line(y, idx, direction)
            self.append_time_series(y)
            self.progress_bar['value'] = 100.0 * (idx + 1) / self.linear_size
            self.redraw()

            if self.scanning:
                if idx + 1 < self.linear_size:
                    self.direction = -self.direction  # keep zig-zag shadow state (GUI doesn’t enforce)
                    self.line_idx = idx + 1
                    print(f"ENQ NEXT LINE: IDX={self.line_idx}")  # <-- log
                    self._request_next_line()
                else:
                    self.toggle_scan()

        elif last_point is not None and not self.scanning:
            self.append_time_series(last_point)
            self.redraw()

        # If idle, ask for new POINT burst every ~150 ms
        #if not self.scanning:
        #    now = time.time()
        #    if now - self._last_point_req > 0.15:
        #        self._request_point(200)
        #        self._last_point_req = now

        self.m.after(30, self._poll_device)


    def _send(self, s: str):
        """Enqueue a text command to the device."""
        self.tx_q.put(s)

    def _request_start(self):
        # Declare a new frame; also ensure bias is set
        self._send(f"START N={self.linear_size}")
        self._send(f"BIAS CODE={20000}")  # TODO: wire to your bias UI if needed

    def _request_next_line(self):
        self._send(f"LINE N={self.linear_size} IDX={self.line_idx}")

    def _request_point(self, count=200):
        self._send(f"POINT COUNT={count}")

    # ----------------- Shutdown -----------------
    '''def _on_close(self):
        self.stop_ev.set()
        try:
            self.sim.join(timeout=0.5)
        except Exception:
            pass
        self.m.destroy()'''
    def _on_close(self):
        self.stop_ev.set()
        try:
            self.serial.join(timeout=0.5)
        except Exception:
            pass
        self.m.destroy()


# ----------------------------- Entrypoint -----------------------------
if __name__ == "__main__":
    m = Tk()
    app = STMApp(m)
    m.mainloop()
