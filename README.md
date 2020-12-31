# Sony PTZ Camera Controller
(based on Python3 and PyQt5)

A simple, somewhat barebones application for controlling Sony PTZ Cameras via IP, specifically the SRG-120DH.

- The camera must be configured for IP mode (as opposed to RS-232 VISCA).
- The software expects the camera to be on 192.168.0.100 - you therefore may have to configure your machine to have an IP in that range, like 192.168.0.101/24. It is possible to set the IP on the camera but I haven't implemented that.
- The software does not read any values from the camera currently, it only sends.
- Some UI components will not have any practical effect unless a dependency mode is set. An example of this would be setting the "brightness" exposure control but being in Full Auto or setting the focus position while being in Auto-Focus mode.
- Lots of keys have shortcuts configured. I've added most of them as tooltip hints. In particular, storing a preset is ctrl+1, ctrl+2, etc. Recalling a preset is just the number straight: 0, 1, 2, etc.

## Installation and Usage
Install the application with
```pip3 install -r requirements.txt```
and run the application like so:
```python3 main.py```

You may want to use a virtual environment:
```
virtualenv sony_ptz
source sony_ptz/bin/activate
pip3 install -r requirements.txt
python3 main.py
```

You may need to install python3, on Ubuntu:
```sudo apt install python3 python3-pip```
