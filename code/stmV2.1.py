from machine import Pin, SPI
import time


stepOut = Pin(0, Pin.OUT)
dirOut = Pin(6, Pin.OUT)

dirSwitch = Pin(3, Pin.IN)
stepPulses = Pin(1, Pin.IN)
stepUp = Pin(4, Pin.IN)
stepDown = Pin(5, Pin.IN)

dacCS = Pin(17, Pin.OUT)
sck = Pin(18, Pin.OUT)
mosi = Pin(19, Pin.OUT)

adcCS = Pin(20, Pin.OUT)
adcSDO = Pin(21, Pin.IN)

dacCS.value(1)
adcCS.value(0)
sck.value(0)
mosi.value(0)

image = []


# pin change interrupt handler for stepPulses, steupUp and stepDown
def stepPulseHandler(pin):
    dirOut.value(not(dirSwitch.value()))
    print(f"stepping")
    stepOut.value(1)
    time.sleep_us(10)
    stepOut.value(0)

def stepUpPulseHandler(pin):
    dirOut.value(0)
    print(f"stepping up")
    stepOut.value(1)
    time.sleep_us(10)
    stepOut.value(0)

def stepDownPulseHandler(pin):
    print(f"stepping down")
    dirOut.value(1)
    stepOut.value(1)
    time.sleep_us(10)
    stepOut.value(0)

stepPulses.irq(trigger=Pin.IRQ_RISING, handler=stepPulseHandler)
stepUp.irq(trigger=Pin.IRQ_RISING, handler=stepUpPulseHandler)
stepDown.irq(trigger=Pin.IRQ_RISING, handler=stepDownPulseHandler)

def dacShiftOut(value):
    dacCS.value(0)
    time.sleep_us(1)
    for i in range(24):
        bit = (value >> (23 - i)) & 0x01
        mosi.value(bit)
        time.sleep_us(1)
        sck.value(1)
        time.sleep_us(1)
        sck.value(0)
        time.sleep_us(1)
    dacCS.value(1)


def setDac(value, channel):
    # value is 16 bit integer, channel is 0, 1, 2, or 3
    
    # command
    command = 0b0011              # write and update
    address = channel & 0b1111    # 4-bit address
    data = value & 0xFFFF

    packet = (command << 20) | (address << 16) | data
    dacShiftOut(packet)

def adcShiftIn():
    value = 0
    for i in range(16):
        sck.value(1)
        #time.sleep_us(1)
        bit = adcSDO.value()
        value = (value << 1) | bit
        sck.value(0)
        #time.sleep_us(1)
    #adcCS.value(1)
    return value

def getADC():
    # start conversion
    adcCS.value(1)
    # wait for conversion to complete
    time.sleep_us(2)
    adcCS.value(0)
    #time.sleep_us(1)
    value = adcShiftIn()
    return value

downScaling = 512
def raster():
    for i in range(0, 65536, downScaling):
        print(i)
        setDac(i, 1)
        image.append([])
        for j in range(0, 65536, downScaling):
            setDac(j, 0)
            time.sleep_us(2)
            image[i//downScaling].append(getADC())

        i += downScaling

        setDac(i, 1)
        image.append([])

        for j in range(65535, -1, -downScaling):
            setDac(j, 0)
            time.sleep_us(2)

            image[i//downScaling].append(getADC())

def rasterZoom():
    for i in range(65536//2 - 64, 65536//2 + 64, 1):
        print(i)
        setDac(i, 1)
        image.append([])
        for j in range(65536//2 - 64, 65536//2 + 64, 1):
            setDac(j, 0)
            time.sleep_us(2)
            image[i - (65536//2 - 64)].append(getADC())

        i += 1

        setDac(i, 1)
        image.append([])

        for j in range(65536//2 + 64, 65536//2 - 64, -1):
            setDac(j, 0)
            time.sleep_us(2)

            image[i - (65536//2 - 64)].append(getADC())

setDac(32767, 0)
setDac(32767, 1)
setDac(32767, 2)
setDac(25000, 3)

print("Ready to raster. Make sure tip is over sample.")
#_ = input("Press Enter to start raster...")
print("Rastering...")
#raster()
rasterZoom()
print(image)

while True:
    pass
    #

#while True:
    #raster()
    #value = int(input("Enter DAC value (0-65535): "))
    #channel = int(input("Enter DAC channel (0-3): "))
    #setDac(value, channel)
    #adc_value = getADC()
    #print(f"ADC Value: {adc_value}")
    #time.sleep(0.2)

