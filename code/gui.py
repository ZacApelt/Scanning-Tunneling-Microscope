from tkinter import *
from tkinter import ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import numpy as np
import time
import threading, queue, time
import serial


def _from_rgb(rgb):
    r, g, b = rgb
    return f'#{r:02x}{g:02x}{b:02x}'


class SerialReader(threading.Thread):
    """
    Background serial reader.
    Reads from a serial port and pushes (x_array, y_array) to a queue.
    """
    def __init__(self, port, baud, out_queue, stop_event, parse_fn, *, timeout=0.5):
        super().__init__(daemon=True)
        self.q = out_queue
        self.stop_event = stop_event
        self.parse_fn = parse_fn
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)

    def run(self):
        buf = bytearray()
        try:
            while not self.stop_event.is_set():
                # Blocking read with timeout
                chunk = self.ser.read(1024)
                if chunk:
                    buf.extend(chunk)
                    # Try to parse as many complete frames as available
                    frames, buf = self.parse_fn(buf)  # returns list[(x, y)], remaining_buf
                    for x_arr, y_arr in frames:
                        # push newest frame only (optional: push all)
                        self.q.put((x_arr, y_arr))
                else:
                    # No data this cycle; small sleep to avoid tight loop
                    time.sleep(0.005)
        finally:
            try:
                self.ser.close()
            except Exception:
                pass


class STMApp:
    def __init__(self, root):
        self.m = root
        self.m.title("Scanning Tunneling Microscope Control")
        self.m.geometry("1400x800")

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

        # Example data
        self.x = np.linspace(-30, 0, 100)
        self.y = np.random.normal(-5, 5, size=100)
        self.Z = np.random.rand(100, 100)

        self.ax1.pcolormesh(self.Z)
        self.ax2.grid()
        self.ax2.set_ylim(-12, 12)
        self.ax2.set_xlim(-30, 0)
        (self.line2,) = self.ax2.plot(self.x, self.y, label="y vs time")

        # histogram (normalised to max=5)
        self.hist_patches = []
        self.update_histogram(self.y, norm_max=1)

        self.fig.tight_layout(pad=0.0)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.main_area)
        self.canvas.draw()
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.main_area)
        self.toolbar.update()
        self.canvas.get_tk_widget().pack(side=TOP, fill=BOTH, expand=1)

        # ----------------- CONTROLS (same layout/packing) -----------------
        # Bias Voltage Slider
        Label(self.side_bar, text="Bias Voltage [V]", bg='lightgrey', font=("Arial", 12)).pack()
        self.bias_slider = Scale(self.side_bar, from_=-3, to=0, orient=HORIZONTAL,
                                 length=400, bg='lightgrey', resolution=0.01,
                                 command=self.on_bias_change)
        self.bias_slider.pack(padx=10, pady=5)

        self.display_duration = 10 # seconds

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        # Vertical Stepper Control
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

        # Set Point
        Label(self.side_bar, text="Set Point [V]", bg='lightgrey', font=("Arial", 12)).pack(pady=5)
        self.setpoint_slider = Scale(self.side_bar, from_=0, to=5, orient=HORIZONTAL,
                                     length=400, bg='lightgrey', resolution=0.01,
                                     command=self.on_setpoint_change)
        self.setpoint_slider.pack(padx=10, pady=5)

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        # Down-sampling multiplier (radio)
        Label(self.side_bar, text="Down-sampling multiplier", bg='lightgrey',
              font=("Arial", 12)).pack(pady=5)
        self.resolution_var = IntVar(value=128)  # default value
        res_frame = Frame(self.side_bar, bg='lightgrey')
        res_frame.pack(pady=5)
        for v in [1, 2, 4, 8, 16, 32, 64, 128, 256]:
            Radiobutton(res_frame, text=f"{v}x", variable=self.resolution_var, value=v,
                        bg='lightgrey', command=self.on_resolution_change).pack(side='left')

        # Zoom (radio)
        Label(self.side_bar, text="Zoom", bg='lightgrey', font=("Arial", 12)).pack(pady=5)
        self.zoom_var = IntVar(value=1)  # default value
        zoom_frame = Frame(self.side_bar, bg='lightgrey')
        zoom_frame.pack(pady=5)
        for v in [1, 2, 4, 8, 16, 32, 64, 128, 256]:
            Radiobutton(zoom_frame, text=f"{v}x", variable=self.zoom_var, value=v,
                        bg='lightgrey', command=self.on_zoom_change).pack(side='left')

        # Scan labels
        self.scan_label = Label(self.side_bar, text="Scan Size: ", bg='lightgrey', font=("Arial", 12))
        self.scan_label.pack(pady=5)
        self.time_label = Label(self.side_bar, text="Estimated Scan Time: ", bg='lightgrey', font=("Arial", 12))
        self.time_label.pack(pady=5)
        self.update_scan_label()  # initial compute

        Label(self.side_bar, text="", bg='lightgrey').pack(pady=5)

        # Time Scale slider
        Label(self.side_bar, text="Time Scale [s]", bg='lightgrey', font=("Arial", 12)).pack(pady=5)
        self.time_scale = Scale(self.side_bar, from_=1, to=60, length=400, orient='horizontal',
                                bg='lightgrey', font=("Arial", 12), command=self.on_time_scale_drag)
        self.time_scale.pack(pady=5)
        self.time_scale.set(10)
        # also snap on release to re-layout tightly
        self.time_scale.bind("<ButtonRelease-1>", lambda e: self.on_time_scale_change())

        # Z-stability
        self.zstab_label = Label(self.side_bar, text="Z-Stability: ", bg='lightgrey', font=("Arial", 12))
        self.zstab_label.pack(pady=5)
        self.update_zstab_label(float(np.std(self.y)))

        # Progress + Start
        self.progress_bar = ttk.Progressbar(self.side_bar, orient='horizontal', length=400, mode='determinate')
        self.progress_bar.pack(padx=10, pady=5, side='bottom')

        self.start_button = Button(self.side_bar, text="Start Scan", bg='green', fg='white',
                                   font=("Arial", 16), command=self.toggle_scan)
        self.start_button.pack(pady=5, ipadx=10, ipady=5, side='bottom')


        

    # ----------------- PLOT HELPERS -----------------
    def update_line_data(self, x, y):
        """Update the line plot data on ax2 and refresh Z-stability."""
        self.x, self.y = x, y
        self.line2.set_data(self.x, self.y)
        # preserve current limits
        xlim = self.ax2.get_xlim()
        ylim = self.ax2.get_ylim()
        self.ax2.set_xlim(xlim)
        self.ax2.set_ylim(ylim)
        self.update_histogram(self.y, norm_max=5)

        self.update_zstab_label(float(np.std(self.y)))
        self.redraw()

    def update_histogram(self, y, norm_max=5):
        """Draw/refresh histogram of y along shared y-axis, normalised so tallest bin == norm_max."""
        # clear old hist artists from ax2_hist
        self.ax2_hist.cla()
        counts, bins, patches = self.ax2_hist.hist(
            y, bins=20, orientation="horizontal", alpha=0.7, color="tab:red"
        )
        if len(counts):
            max_count = max(counts.max(), 1.0)
            scale = float(norm_max) / max_count
            # scale patch widths
            for rect in patches:
                rect.set_width(rect.get_width() * scale)
            self.ax2_hist.set_xlim(0, norm_max)
        else:
            self.ax2_hist.set_xlim(0, norm_max)

        # styling & alignment with main y-range
        self.ax2_hist.xaxis.set_visible(False)
        self.ax2_hist.set_ylim(self.ax2.get_ylim())

    def set_time_window(self, seconds):
        """Adjust the time window on ax2 (x-axis)."""
        self.ax2.set_xlim(-float(seconds), 0)


    def update_scan_grid(self, N):
        """Regenerate the pcolormesh grid size on ax1 to N x N."""
        #print(N)
        self.ax1.clear()
        self.Z = np.random.rand(N, N)
        self.ax1.pcolormesh(self.Z)

    def redraw(self, tight=True):
        if tight:
            self.fig.tight_layout(pad=0.0)
        self.canvas.draw_idle()

    # ----------------- UI CALLBACKS -----------------
    def on_bias_change(self, val):
        bias = float(val)
        # TODO: hook into hardware/software control
        print(f"[CB] Bias changed -> {bias:.2f} V")

    def on_setpoint_change(self, val):
        sp = float(val)
        # TODO: hook into control loop setpoint
        print(f"[CB] Setpoint changed -> {sp:.2f} V")

    def on_step(self, action):
        # TODO: hook into vertical stepper/jog motion
        print(f"[CB] Stepper action -> {action}")

    def on_resolution_change(self):
        self.update_scan_label()

    def on_zoom_change(self):
        self.update_scan_label()

    def on_time_scale_drag(self, val):
        # live drag feedback (no tight_layout to keep it responsive)
        self.display_duration = float(val)
        self.set_time_window(float(val))
        self.canvas.draw_idle()

    def on_time_scale_change(self):
        # commit change with tight layout
        self.set_time_window(self.time_scale.get())
        self.redraw()

    def toggle_scan(self):
        if self.start_button.cget('text') == 'Start Scan':
            self.start_button.config(text='Stop Scan', bg='red')
            # self.progress_bar.start()  # real scan would start
            self.progress_bar['value'] = 50  # demo
            print("[CB] Scan started")

            self.update_scan_grid(self.linear_size)
            self.redraw()
        else:
            self.start_button.config(text='Start Scan', bg='green')
            self.progress_bar.stop()
            self.progress_bar['value'] = 0
            print("[CB] Scan stopped")

    # ----------------- STATUS/INFO -----------------
    def update_scan_label(self):
        zoom = int(self.zoom_var.get())
        res = int(self.resolution_var.get())
        self.linear_size = 65536 // (zoom * res)
        self.scan_label.config(text=f"Scan Size: {self.linear_size} x {self.linear_size}")

        # Update ax1 grid to reflect resolution (same layout)
        self.update_scan_grid(self.linear_size)

        time_per_pixel_ms = 0.01  # ms
        total_seconds = int(((self.linear_size ** 2) * time_per_pixel_ms) // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        self.time_label.config(text=f"Estimated Scan Time: {minutes} min {seconds} s")
        self.redraw()

    def update_zstab_label(self, std):
        self.zstab_label.config(text=f"Z-Stability: {std:.2f}")
        self.zstab_label.config(fg='green' if std < 0.5 else 'red')


if __name__ == "__main__":
    m = Tk()
    app = STMApp(m)
    m.mainloop()
