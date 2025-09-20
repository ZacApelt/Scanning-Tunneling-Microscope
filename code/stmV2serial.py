from machine import Pin
import time
import sys
import uselect

# ---------------- GPIO / DAC / ADC wiring ----------------
stepOut = Pin(0, Pin.OUT)
dirOut  = Pin(6, Pin.OUT)

dirSwitch   = Pin(3, Pin.IN)
stepPulses  = Pin(1, Pin.IN)
stepUp      = Pin(4, Pin.IN)
stepDown    = Pin(5, Pin.IN)

dacCS = Pin(17, Pin.OUT)
sck   = Pin(18, Pin.OUT)
mosi  = Pin(19, Pin.OUT)

adcCS = Pin(20, Pin.OUT)
adcSDO= Pin(21, Pin.IN)

dacCS.value(1)
adcCS.value(0)
sck.value(0)
mosi.value(0)

# ---------------- Config / State ----------------
# DAC channels: 0:X, 1:Y, 2:spare, 3:Bias
BIAS_CH = 3
X_CH = 0
Y_CH = 1

# Scan/session state
frame_N = 128          # default; set by START or first LINE
cur_line_idx = 0
cur_dir = +1           # device-chosen zig-zag
bias_code = 20000      # default (constant unless BIAS command)

# Timing
PIXEL_SETTLE_US = 150  # settle after setting DAC before ADC read
POINT_RATE_US   = 200  # time between POINT samples

# ---------------- Stepper IRQs (your existing helpers) ----------------
def stepPulseHandler(pin):
    dirOut.value(not dirSwitch.value())
    stepOut.value(1); time.sleep_us(10); stepOut.value(0)

def stepUpPulseHandler(pin):
    dirOut.value(0)
    stepOut.value(1); time.sleep_us(10); stepOut.value(0)

def stepDownPulseHandler(pin):
    dirOut.value(1)
    stepOut.value(1); time.sleep_us(10); stepOut.value(0)

stepPulses.irq(trigger=Pin.IRQ_RISING, handler=stepPulseHandler)
stepUp.irq(trigger=Pin.IRQ_RISING, handler=stepUpPulseHandler)
stepDown.irq(trigger=Pin.IRQ_RISING, handler=stepDownPulseHandler)

# ---------------- Low-level DAC/ADC ----------------
def dacShiftOut(value):
    dacCS.value(0)
    for i in range(24):
        mosi.value((value >> (23 - i)) & 1)
        time.sleep_us(1)
        sck.value(1)
        time.sleep_us(1)
        sck.value(0)
        time.sleep_us(1)
    dacCS.value(1)

def setDac(code16, channel):
    # command (0011 = write & update), 4-bit address, 16-bit data
    packet = (0b0011 << 20) | ((channel & 0xF) << 16) | (code16 & 0xFFFF)
    dacShiftOut(packet)

def adcShiftIn():
    val = 0
    for _ in range(16):
        sck.value(1)
        val = (val << 1) | adcSDO.value()
        sck.value(0)
    return val

def getADC():
    adcCS.value(1)
    time.sleep_us(2)
    adcCS.value(0)
    return adcShiftIn()

def set_bias(code):
    global bias_code
    bias_code = int(code) & 0xFFFF
    setDac(bias_code, BIAS_CH)

# ---------------- Helpers ----------------
def clamp_u16(x): return 0 if x < 0 else (65535 if x > 65535 else int(x))

def lin_code(pos, N):
    """Map pixel index 0..N-1 to 0..65535 DAC code."""
    if N <= 1: return 0
    return clamp_u16((pos * 65535) // (N - 1))

def read_height_avg(samples=1):
    """Return one height reading as float; simple average of 'samples' ADC reads."""
    acc = 0
    for _ in range(samples):
        acc += getADC()
    # Convert 16-bit ADC code to an arbitrary 'height' unit (centered around 0).
    # You can replace this with your calibrated conversion.
    code = acc / float(samples)
    return (code - 32768.0) / 4096.0  # ~ +/- 8 units range

# ---------------- Protocol (Text/CSV) ----------------
# Commands supported:
#   START N=<int>
#   LINE N=<int> IDX=<int>
#   POINT COUNT=<int>
#   BIAS CODE=<int>
#   STATUS
#
# Replies:
#   LINE OK N=.. IDX=.. DIR=..
#   <csv of N floats>
#   POINT OK COUNT=..
#   <csv of COUNT floats>
#   OK MSG="..."
#   ERR CODE=<int> MSG="..."

def parse_kv(parts):
    kv = {}
    for p in parts:
        if '=' in p:
            k, v = p.split('=', 1)
            kv[k.strip().upper()] = v.strip()
    return kv

def cmd_START(kv):
    global frame_N, cur_line_idx, cur_dir
    if 'N' not in kv:
        print('ERR CODE=10 MSG="START requires N"')
        return
    n = int(kv['N'])
    if n < 2 or n > 4096:
        print('ERR CODE=11 MSG="N out of range"'); return
    frame_N = n
    cur_line_idx = 0
    cur_dir = +1
    print('OK MSG="start-ready"')

def cmd_BIAS(kv):
    if 'CODE' not in kv:
        print('ERR CODE=20 MSG="BIAS requires CODE"'); return
    try:
        code = int(kv['CODE'])
    except:
        print('ERR CODE=21 MSG="BIAS CODE invalid"'); return
    set_bias(code)
    print('OK MSG="bias-set"')

def cmd_STATUS():
    print('OK MSG="ready" N=%d IDX=%d DIR=%+d BIAS_CODE=%d' % (frame_N, cur_line_idx, cur_dir, bias_code))

def cmd_POINT(kv):
    cnt = int(kv.get('COUNT', '200'))
    cnt = 1 if cnt < 1 else (4096 if cnt > 4096 else cnt)

    ys = []
    for _ in range(cnt):
        time.sleep_us(POINT_RATE_US)
        ys.append(read_height_avg(1))

    print('POINT OK COUNT=%d' % cnt)
    # one CSV line; print adds '\n' and is flushed by the REPL transport
    print(','.join('%.6f' % y for y in ys))

def cmd_LINE(kv):
    global frame_N, cur_line_idx, cur_dir
    if 'N' not in kv or 'IDX' not in kv:
        print('ERR CODE=30 MSG="LINE requires N and IDX"'); return
    N = int(kv['N'])
    IDX = int(kv['IDX'])
    if N < 2 or N > 4096:
        print('ERR CODE=31 MSG="N out of range"'); return
    if IDX < 0 or IDX >= N:
        print('ERR CODE=32 MSG="IDX out of range"'); return

    # adopt GUI N if it differs
    frame_N = N
    cur_line_idx = IDX
    # Device chooses zig-zag: even rows forward, odd rows reverse
    cur_dir = +1 if (IDX % 2 == 0) else -1

    # Move Y to the correct row position
    y_code = lin_code(IDX, N)
    setDac(y_code, Y_CH)
    # Ensure bias is set (sticky)
    setDac(bias_code, BIAS_CH)

    # Sweep X across the line with chosen direction
    ys = []
    if cur_dir > 0:
        x_iter = range(N)
    else:
        x_iter = range(N-1, -1, -1)

    for i in x_iter:
        x_code = lin_code(i, N)
        setDac(x_code, X_CH)
        time.sleep_us(PIXEL_SETTLE_US)
        ys.append(read_height_avg(1))

    # after acquiring ys (in acquisition order):
    print('LINE OK N=%d IDX=%d DIR=%+d' % (N, IDX, cur_dir))
    # one CSV line
    print(','.join('%.6f' % y for y in ys))

# ---------------- Main loop: non-blocking serial ----------------
setDac(32767, X_CH)
setDac(32767, Y_CH)
setDac(32767, 2)
set_bias(bias_code)

spoll = uselect.poll()
spoll.register(sys.stdin, uselect.POLLIN)

def handle_line(line):
    parts = [p for p in line.strip().split() if p]
    if not parts:
        return
    cmd = parts[0].upper()
    kv = parse_kv(parts[1:])

    try:
        if cmd == 'START':
            cmd_START(kv)
        elif cmd == 'LINE':
            cmd_LINE(kv)
        elif cmd == 'POINT':
            cmd_POINT(kv)
        elif cmd == 'BIAS':
            cmd_BIAS(kv)
        elif cmd == 'STATUS':
            cmd_STATUS()
        else:
            print('ERR CODE=1 MSG="unknown command"')
    except Exception as e:
        print('ERR CODE=99 MSG="exception: %s"' % str(e))

try:
    # Optional: send a hello once
    print('OK MSG="pico-ready"')

    buf = ""
    while True:
        # Non-blocking read of a line
        if spoll.poll(0):
            ch = sys.stdin.read(1)
            if ch:
                if ch == '\n':
                    handle_line(buf)
                    buf = ""
                else:
                    buf += ch

        # You can put low-priority background tasks here (e.g., watchdog)
        # time.sleep_ms(1)

except Exception as e:
    # On crash, safe DACs
    for ch in (0,1,2,3):
        setDac(0, ch)
