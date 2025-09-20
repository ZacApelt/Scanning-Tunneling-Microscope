# Scanning-Tunneling-Microscope
Code, design files and resources I used to build a scanning tunnelling microscope.

Originally, a GUI was created, but it was not fully implemented with serial. The process of scanning involved manually initiating quantum tunnelling using the coarse adjust scres and manual stepper motor controls. The Z testpoint was measured on a scope, and once it stabilised, a scan was started using stmV2.1.py running on the pico rp2040. Once a scan is complete, the data is printed to the terminal and copied into dataProcessor.py, which generates the plots.

The code running on the Pico is responsible for interfacing with the ADC, DAC, and controlling the stepper motors. Zoom and downsampling are adjusted by changing the limits of the for loops. 

The main PCB connects the Pico to the DAC and ADC, and contains all the analog electronics for running the analog integrator control loop, and mixing the X, Y, and Z signals into the 4 quadrants of the piezo ceramic. An analog control loop was chosen over a more conventional digital control loop because the controller needs to respond very quickly to changes in tunnelling current. The tip is held at about 0V and the sample is biased to between -3V and 0V to allow quantum tunnelling to occur. Many bodges on this PCB were required so the gerber files are not included, but the schematics have been corrected.

A pre-amp is attached physically close to the tip. This is a simple trans-impedance amplifier using an op-amp with very low input bias current (AD8641ARZ with a typical input bias of 0.25pA). Typically, a higher scan resolution requires lower sample bias, which lowers the tunnelling current, so low input bias current and low noise is very important. The amplifier has a gain of 10^8 and leakage currents on the PCB are a considerable problem. A through-hole 100Mohm resistor was soldered directly to the inverting input of the op-amp, and that leg was raised so that the pin does not touch the PCB.

The tip is required to be atomically sharp. It was made out of 0.5mm tungsten wire (I would recommend using thinner wire) and was cut while under tension. This attaches to the piezo disk using a JST-XH pin and socket. The tip needs to be electrically isolated from the piezo disk so a glass microscope slide coverslip was cut into 2 pieces, each to a size of about 5mm x 5mm. CA glue was used to stack them together, then glued to the piezo disk and the tip was glued to the glass insulator. Current leakage through the glue is also an issue.

The height of the tip is adjusted using a kinematic mount made using M6 bolts with the end drilled out slightly to press fit 3mm bearings into the end. The drilling operation was done on a lathe to ensure concentricity. The 3 adjustment screws each had a unique mating surface, one a countersink to constrain XY motion, one a slide rail to constrain X motion and the other a flat surface to not constrain any direction. This provides very smooth positioning of height and 3 rotational degrees of freedom. The tip is located 1mm further inwards than the two coarse adjustment screws, and the fine adjust is 80mm away, giving a mechanical advantage of 80:1. Springs are used to keep the top and bottom plates seated correctly and help to remove backlash in the threads. The stepper motor is rigidly attached to the lower plate, and a spring coupling is used to translate the rotational motion from the stepper to the screw, which do not maintain colinearity when the top plate is rotated.

[onshape CAD](https://cad.onshape.com/documents/35bc17e515975e1f630af773/w/ea88894951b38c973bfc53a6/e/81147cb30bf4feeda1627577?renderMode=0&uiState=68ceb060467be60fe1d4c091)

[GRABCAD](https://grabcad.com/library/scanning-tunneling-microscope-1)

![20250920_235328](https://github.com/user-attachments/assets/7d60cb5b-465e-4fd6-90e9-21ad7daf3f5d)

<img width="909" height="708" alt="Screenshot 2025-09-21 004750" src="https://github.com/user-attachments/assets/7660440d-65dc-4eb7-a645-428b069c5338" />

<img width="570" height="709" alt="Screenshot 2025-09-21 004819" src="https://github.com/user-attachments/assets/2da44dce-cd29-493d-9049-edf076c3323b" />

<img width="1920" height="975" alt="AlSurfaceRoughness" src="https://github.com/user-attachments/assets/8e6813fc-d8ef-406b-871e-0006471d11cc" />
<img width="1920" height="975" alt="atoms" src="https://github.com/user-attachments/assets/5b0a056f-111f-4db1-bb0d-14c58088a40b" />
