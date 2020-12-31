#!/usr/bin/python3
import os
import signal
import sys
import copy
import socket
import time
import binascii
from PyQt5 import uic
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import (QCoreApplication, QObject, QRunnable, QThread,
                          QThreadPool, pyqtSignal, pyqtSlot)
from PyQt5.uic.properties import QtCore

signal.signal(signal.SIGINT, signal.SIG_DFL)


class Command:
    data = []
    arg_lambda = None

    def __init__(self, data, argument_lambda=None):
        self.data = data
        self.arg_lambda = argument_lambda

    def get_command(self, args):
        if self.arg_lambda is not None:
            return self.arg_lambda(self, args)

        return self.data


def preset(self, args):
    self.data[5] = args
    return self.data


def pan_relative_lambda(self, args):
    data = copy.deepcopy(self.data)
    # 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF
    # VV WW 0Y 0Y 0Y 0Y 0Z 0Z 0Z 0Z FF

    speed_pan = int(args["speed"] * 0x17) + 1
    speed_tilt = int(args["speed"] * 0x13) + 1

    data.extend([speed_pan, speed_tilt])  # half speed | between 0x01 and 0x18, 0x01 and 0x14
    data.extend([0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0xFF])
    return data


def oneshot_ptz_lambda(self, args):
    data = copy.deepcopy(self.data)
    # 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF
    # VV WW 0Y 0Y 0Y 0Y 0Z 0Z 0Z 0Z FF

    speed_pan = int(args["speed"] * 0x17) + 1
    speed_tilt = int(args["speed"] * 0x13) + 1

    data.extend([speed_pan, speed_tilt])  # half speed | between 0x01 and 0x18, 0x01 and 0x14
    data.extend(args["postfix"])
    return data


def zoom_tele_variable(self, args):
    speed = int(args["speed"] * 6) + 1

    self.data[4] = 0x20 + speed

    return self.data


def zoom_wide_variable(self, args):
    speed = int(args["speed"] * 6) + 1

    self.data[4] = 0x30 + speed

    return self.data


def exposure_command(self, args):
    data = copy.deepcopy(self.data)

    data[6] = int(args / 16)
    data[7] = args & 0x0F

    return data


def ae_mode_lambda(self, args):
    modes = {
        "Full Auto": 0x00,
        "Manual": 0x03,
        "Tv": 0x0A,
        "Av": 0x0B,
        "Brightness": 0x0D
    }

    new_data = copy.deepcopy(self.data)
    new_data[4] = modes[args]

    return new_data


def wb_mode_lambda(self, args):
    modes = {
        "Auto": 0x00,
        "Indoor": 0x01,
        "Outdoor": 0x02,
        "One Push": 0x03,  # Mode Set
        "Auto Tracking": 0x04,
        "Manual": 0x05
    }

    new_data = copy.deepcopy(self.data)
    new_data[4] = modes[args]

    return new_data


class Camera(QRunnable):
    address = "192.168.0.100"
    port = 52381
    current_sequence_number = 0
    sequence_callback = None
    ui = None
    receive_socket = None

    commands = dict()

    commands["backlight on"] = Command([0x81, 0x01, 0x04, 0x33, 0x03, 0xFF])
    commands["backlight off"] = Command([0x81, 0x01, 0x04, 0x33, 0x02, 0xFF])
    commands["set preset x"] = Command([0x81, 0x01, 0x04, 0x3F, 0x01, 0x00, 0xFF], argument_lambda=preset)
    commands["recall preset x"] = Command([0x81, 0x01, 0x04, 0x3F, 0x02, 0x00, 0xFF], argument_lambda=preset)
    commands["home"] = Command([0x81, 0x01, 0x06, 0x04, 0xFF])
    commands["pan relative position"] = Command(
        [0x81, 0x01, 0x06, 0x03], argument_lambda=pan_relative_lambda)
    commands["move"] = Command([0x81, 0x01, 0x06, 0x01], argument_lambda=oneshot_ptz_lambda)
    commands["stop"] = Command([0x81, 0x01, 0x06, 0x01, 0x01, 0x01, 0x03, 0x03, 0xFF])
    commands["osd on"] = Command([0x81, 0x01, 0x7E, 0x01, 0x18, 0x02, 0xFF])
    commands["osd off"] = Command([0x81, 0x01, 0x7E, 0x01, 0x18, 0x03, 0xFF])
    commands["low latency on"] = Command([0x81, 0x01, 0x7E, 0x01, 0x5A, 0x02, 0xFF])
    commands["low latency off"] = Command([0x81, 0x01, 0x7E, 0x01, 0x5A, 0x03, 0xFF])
    commands["zoom stop"] = Command([0x81, 0x01, 0x04, 0x07, 0x00, 0xFF])
    commands["digital zoom on"] = Command([0x81, 0x01, 0x04, 0x06, 0x02, 0xFF])
    commands["digital zoom off"] = Command([0x81, 0x01, 0x04, 0x06, 0x03, 0xFF])
    commands["zoom tele std"] = Command([0x81, 0x01, 0x04, 0x07, 0x02, 0xFF])
    commands["zoom wide std"] = Command([0x81, 0x01, 0x04, 0x07, 0x03, 0xFF])
    commands["zoom tele var"] = Command([0x81, 0x01, 0x04, 0x07, 0x00, 0xFF], argument_lambda=zoom_tele_variable)
    commands["zoom wide var"] = Command([0x81, 0x01, 0x04, 0x07, 0x00, 0xFF], argument_lambda=zoom_wide_variable)
    commands["ae mode"] = Command([0x81, 0x01, 0x04, 0x39, 0x00, 0xFF],
                                  argument_lambda=ae_mode_lambda)
    commands["brighter"] = Command([0x81, 0x01, 0x04, 0x0D, 0x02, 0xFF])
    commands["darker"] = Command([0x81, 0x01, 0x04, 0x0D, 0x03, 0xFF])

    commands["af mode auto"] = Command([0x81, 0x01, 0x04, 0x38, 0x02, 0xFF])
    commands["af mode manual"] = Command([0x81, 0x01, 0x04, 0x38, 0x03, 0xFF])
    commands["af mode auto/manual"] = Command([0x81, 0x01, 0x04, 0x38, 0x10, 0xFF])
    commands["af mode one push trigger"] = Command([0x81, 0x01, 0x04, 0x18, 0x01, 0xFF])
    commands["focus far var"] = Command([0x81, 0x01, 0x04, 0x08, 0x00, 0xFF], argument_lambda=zoom_tele_variable)
    commands["focus near var"] = Command([0x81, 0x01, 0x04, 0x08, 0x00, 0xFF], argument_lambda=zoom_wide_variable)
    commands["focus stop"] = Command([0x81, 0x01, 0x04, 0x08, 0x00, 0xFF])
    commands["wb mode"] = Command([0x81, 0x01, 0x04, 0x35, 0x00, 0xFF], argument_lambda=wb_mode_lambda)
    commands["wb mode trigger"] = Command([0x81, 0x01, 0x04, 0x10, 0x05, 0xFF])
    commands["red gain up"] = Command([0x81, 0x01, 0x04, 0x03, 0x02, 0xFF])
    commands["red gain down"] = Command([0x81, 0x01, 0x04, 0x03, 0x03, 0xFF])
    commands["red gain reset"] = Command([0x81, 0x01, 0x04, 0x03, 0x00, 0xFF])
    commands["blue gain up"] = Command([0x81, 0x01, 0x04, 0x04, 0x02, 0xFF])
    commands["blue gain down"] = Command([0x81, 0x01, 0x04, 0x04, 0x03, 0xFF])
    commands["blue gain reset"] = Command([0x81, 0x01, 0x04, 0x04, 0x00, 0xFF])
    commands["gain set"] = Command([0x81, 0x01, 0x04, 0x4C, 0x00, 0x00, 0x00, 0x00, 0xFF],
                                   argument_lambda=exposure_command)
    commands["shutter set"] = Command([0x81, 0x01, 0x04, 0x4A, 0x00, 0x00, 0x00, 0x00, 0xFF],
                                      argument_lambda=exposure_command)
    commands["fstop set"] = Command([0x81, 0x01, 0x04, 0x4B, 0x00, 0x00, 0x00, 0x00, 0xFF],
                                    argument_lambda=exposure_command)
    commands["ex_ae_comp set"] = Command([0x81, 0x01, 0x04, 0x4E, 0x00, 0x00, 0x00, 0x00, 0xFF],
                                         argument_lambda=exposure_command)
    commands["ex ae comp on"] = Command([0x81, 0x01, 0x04, 0x03E, 0x02, 0xFF])
    commands["ex ae comp off"] = Command([0x81, 0x01, 0x04, 0x3E, 0x03, 0xFF])

    # ex_ae_comp
    # gain         8x 01 04 4C 00 00 0p 0q FF
    # shutter      8x 01 04 4A 00 00 0p 0q FF
    # fstop        8x 01 04 4B 00 00 0p 0q FF

    def __init__(self, address="192.168.0.100", port=52381, sequence_callback=None):
        super(Camera, self).__init__()
        self.address = address
        self.port = port
        self.sequence_callback = sequence_callback

        self.receive_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receive_socket.bind(("0.0.0.0", self.port))

        # self.send_command([0x01], prep_cmd=[0x02, 0x01])
        # socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(bytes(prep_cmd), (self.address, self.port))
        self.reset()

    def reset(self):
        self.current_sequence_number = 0
        self.send_control_command([0x01])
        self.current_sequence_number = 0

    def increment_sequence(self):
        self.current_sequence_number = self.current_sequence_number + 1
        if self.sequence_callback is not None:
            self.sequence_callback(self.current_sequence_number)
        # self.current_sequence_number = 0xFFFFFFFF

    def send_control_command(self, cmd):
        buffy = bytearray([0x02, 0x00])
        buffy.extend(bytearray(len(cmd).to_bytes(length=2, byteorder="little", signed=False)))
        self.current_sequence_number = (self.current_sequence_number + 1) % 0xFFFFFFFF
        buffy.extend(bytearray(self.current_sequence_number.to_bytes(length=4, byteorder="little", signed=False)))
        buffy.extend(bytearray(cmd))

        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(bytes(buffy), (self.address, self.port))

    def send_command(self, command, header=[0x01, 0x00]):
        self.increment_sequence()
        prep_cmd = copy.deepcopy(header)
        prep_cmd.extend(bytearray([0x00, int(len(command))]))  # payload type (2), followed by command length
        prep_cmd.extend(bytearray(self.current_sequence_number.to_bytes(length=4, byteorder="little", signed=False)))
        prep_cmd.extend(command)
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(bytes(prep_cmd), (self.address, self.port))

    def run(self):
        while True:
            data, addr = self.receive_socket.recvfrom(1024)
            # bad command response 0200 0002ba 0000000f01
            # good cmmand response 0111 0003d3 0000009041ff
            data = bytearray(data)
            curr_seq_num = int.from_bytes(data[2:5], "big", signed=False)
            print("SEQ " + str(curr_seq_num))
            print(binascii.hexlify(data[2:5]))
            # self.current_sequence_number = curr_seq_num
            print(binascii.hexlify(data))
            print(self.current_sequence_number)
            time.sleep(0.1)


class App(QApplication):

    def __init__(self):
        super(App, self).__init__([])
        self.ui = uic.loadUi("main.ui")
        cam = Camera()
        self.cam = cam

    def event(self, event):
        print(event.type())
        if event.type() == 20:
            os._exit(0)

        return False

    def main(self):
        ui = self.ui
        ui = self.ui
        cam = self.cam
        cam.ui = ui

        # QThreadPool.globalInstance().setMaxThreadCount(50)
        QThreadPool.globalInstance().start(cam)

        # Presets
        ui.setzero.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(0)))
        ui.getzero.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(0)))
        ui.setone.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(1)))
        ui.getone.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(1)))
        ui.settwo.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(2)))
        ui.gettwo.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(2)))
        ui.setthree.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(3)))
        ui.getthree.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(3)))
        ui.setfour.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(4)))
        ui.getfour.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(4)))
        ui.setfive.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(5)))
        ui.getfive.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(5)))
        ui.setsix.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(6)))
        ui.getsix.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(6)))
        ui.setseven.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(7)))
        ui.getseven.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(7)))
        ui.seteight.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(8)))
        ui.geteight.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(8)))
        ui.setnine.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(9)))
        ui.getnine.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(9)))
        ui.setten.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(10)))
        ui.getten.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(10)))
        ui.seteleven.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(11)))
        ui.geteleven.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(11)))
        ui.settwelve.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(12)))
        ui.gettwelve.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(12)))
        ui.setthirteen.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(13)))
        ui.getthirteen.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(13)))
        ui.setfourteen.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(14)))
        ui.getfourteen.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(14)))
        ui.setfifteen.clicked.connect(lambda: cam.send_command(cam.commands["set preset x"].get_command(15)))
        ui.getfifteen.clicked.connect(lambda: cam.send_command(cam.commands["recall preset x"].get_command(15)))

        ui.ae_mode.currentTextChanged.connect(
            lambda: cam.send_command(cam.commands["ae mode"].get_command(ui.ae_mode.currentText())))

        ui.brighter.clicked.connect(lambda: cam.send_command(cam.commands["brighter"].get_command(None)))
        ui.darker.clicked.connect(lambda: cam.send_command(cam.commands["darker"].get_command(None)))
        ui.wb_trigger.clicked.connect(lambda: cam.send_command(cam.commands["wb mode trigger"].get_command(None)))
        ui.wb_mode.currentTextChanged.connect(
            lambda: cam.send_command(cam.commands["wb mode"].get_command(ui.wb_mode.currentText())))

        ui.red_gain_more.clicked.connect(lambda: cam.send_command(cam.commands["red gain up"].get_command(None)))
        ui.red_gain_less.clicked.connect(lambda: cam.send_command(cam.commands["red gain down"].get_command(None)))
        ui.red_gain_reset.clicked.connect(lambda: cam.send_command(cam.commands["red gain reset"].get_command(None)))

        ui.blue_gain_more.clicked.connect(lambda: cam.send_command(cam.commands["blue gain up"].get_command(None)))
        ui.blue_gain_less.clicked.connect(lambda: cam.send_command(cam.commands["blue gain down"].get_command(None)))
        ui.blue_gain_reset.clicked.connect(lambda: cam.send_command(cam.commands["blue gain reset"].get_command(None)))

        def af_mode():
            modeFuncs = {
                "On": cam.commands["af mode auto"].get_command(None),
                "Manual": cam.commands["af mode manual"].get_command(None),
                "Auto/Manual": cam.commands["af mode auto/manual"].get_command(None),
                "One Push Trigger": cam.commands["af mode one push trigger"].get_command(None)
            }

            cam.send_command(modeFuncs[ui.af_mode.currentText()])

        ui.af_mode.currentTextChanged.connect(af_mode)

        fstop = {
            "f/1.8": cam.commands["fstop set"].get_command(0x11),
            "f/2.0": cam.commands["fstop set"].get_command(0x10),
            "f/2.4": cam.commands["fstop set"].get_command(0x0F),
            "f/2.8": cam.commands["fstop set"].get_command(0x0E),
            "f/3.4": cam.commands["fstop set"].get_command(0x0D),
            "f/4.0": cam.commands["fstop set"].get_command(0x0C),
            "f/4.8": cam.commands["fstop set"].get_command(0x0B),
            "f/5.6": cam.commands["fstop set"].get_command(0x0A),
            "f/6.8": cam.commands["fstop set"].get_command(0x09),
            "f/8.0": cam.commands["fstop set"].get_command(0x08),
            "f/9.6": cam.commands["fstop set"].get_command(0x07),
            "f/11.0": cam.commands["fstop set"].get_command(0x06),
            "f/14.0": cam.commands["fstop set"].get_command(0x05),
            "Closed": cam.commands["fstop set"].get_command(0x00),
        }

        ui.fstop.clear()
        ui.fstop.addItems(fstop.keys())
        ui.fstop.currentTextChanged.connect(lambda: cam.send_command(fstop[ui.fstop.currentText()]))

        shutter = {
            "1/250 | 1/215": cam.commands["shutter set"].get_command(0x0B),
            "1/180 | 1/150": cam.commands["shutter set"].get_command(0x0A),
            "1/125 | 1/120": cam.commands["shutter set"].get_command(0x09),
            "1/100 | 1/100": cam.commands["shutter set"].get_command(0x08),
            "1/90 | 1/75": cam.commands["shutter set"].get_command(0x07),
            "1/60 | 1/50": cam.commands["shutter set"].get_command(0x06),
            "1/30 | 1/25": cam.commands["shutter set"].get_command(0x05),
        }

        ui.shutter.clear()
        ui.shutter.addItems(shutter.keys())
        ui.shutter.currentTextChanged.connect(lambda: cam.send_command(shutter[ui.shutter.currentText()]))

        ex_gain = {
            "0dB": cam.commands["gain set"].get_command(0x01),
            "3dB": cam.commands["gain set"].get_command(0x02),
            "6dB": cam.commands["gain set"].get_command(0x03),
            "9dB": cam.commands["gain set"].get_command(0x04),
            "12dB": cam.commands["gain set"].get_command(0x05),
            "15dB": cam.commands["gain set"].get_command(0x06),
            "18dB": cam.commands["gain set"].get_command(0x07),
            "21dB": cam.commands["gain set"].get_command(0x08),
            "24dB": cam.commands["gain set"].get_command(0x09),
            "27dB": cam.commands["gain set"].get_command(0x0A),
            "30dB": cam.commands["gain set"].get_command(0x0B),
            "33dB": cam.commands["gain set"].get_command(0x0C),
            "36dB": cam.commands["gain set"].get_command(0x0D),
            "39dB": cam.commands["gain set"].get_command(0x0E),
            "43dB": cam.commands["gain set"].get_command(0x0F),
        }

        ui.gain.clear()
        ui.gain.addItems(ex_gain.keys())
        ui.gain.currentTextChanged.connect(lambda: cam.send_command(ex_gain[ui.gain.currentText()]))

        ex_ae_comps = {
            "-10.5dB": cam.commands["ex_ae_comp set"].get_command(0x00),
            "-9dB": cam.commands["ex_ae_comp set"].get_command(0x01),
            "-7.5dB": cam.commands["ex_ae_comp set"].get_command(0x02),
            "-6dB": cam.commands["ex_ae_comp set"].get_command(0x03),
            "-4.5dB": cam.commands["ex_ae_comp set"].get_command(0x04),
            "-3dB": cam.commands["ex_ae_comp set"].get_command(0x05),
            "-1.5dB": cam.commands["ex_ae_comp set"].get_command(0x06),
            "0dB": cam.commands["ex_ae_comp set"].get_command(0x07),
            "+1.5dB": cam.commands["ex_ae_comp set"].get_command(0x08),
            "+3dB": cam.commands["ex_ae_comp set"].get_command(0x09),
            "+4.5dB": cam.commands["ex_ae_comp set"].get_command(0x0A),
            "+6dB": cam.commands["ex_ae_comp set"].get_command(0x0B),
            "+7.5dB": cam.commands["ex_ae_comp set"].get_command(0x0C),
            "+9dB": cam.commands["ex_ae_comp set"].get_command(0x0D),
            "+10.5dB": cam.commands["ex_ae_comp set"].get_command(0x0E),
        }

        ui.ex_comp.clear()
        ui.ex_comp.addItems(ex_ae_comps.keys())
        ui.ex_comp.currentTextChanged.connect(lambda: cam.send_command(ex_ae_comps[ui.ex_comp.currentText()]))

        def seq_callback(seq):
            ui.seq_number.display(seq)

        cam.sequence_callback = seq_callback

        def osd_func():
            if ui.osd.checkState():
                cam.send_command(cam.commands["osd on"].get_command(None))
            else:
                cam.send_command(cam.commands["osd off"].get_command(None))

        ui.osd.clicked.connect(osd_func)

        def ex_ae_enabled_func():
            if ui.ex_ae_comp_on.checkState():
                cam.send_command(cam.commands["ex ae comp on"].get_command(None))
            else:
                cam.send_command(cam.commands["ex ae comp off"].get_command(None))

        ui.ex_ae_comp_on.clicked.connect(ex_ae_enabled_func)

        def digital_zoom_func():
            if ui.digital_zoom.checkState():
                cam.send_command(cam.commands["digital zoom on"].get_command(None))
            else:
                cam.send_command(cam.commands["digital zoom off"].get_command(None))

        ui.digital_zoom.clicked.connect(digital_zoom_func)

        def low_latency_func():
            if ui.low_latency.checkState():
                cam.send_command(cam.commands["low latency on"].get_command(None))
            else:
                cam.send_command(cam.commands["low latency off"].get_command(None))

        ui.low_latency.clicked.connect(low_latency_func)

        ui.up.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x03, 0x01, 0xFF],

                }
            )))
        ui.down.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x03, 0x02, 0xFF],

                }
            )))
        ui.left.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x01, 0x03, 0xFF],

                }
            )))
        ui.right.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x02, 0x03, 0xFF],

                }
            )))
        ui.ul.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x01, 0x01, 0xFF],

                }
            )))
        ui.ur.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x02, 0x01, 0xFF],

                }
            )))
        ui.dl.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x01, 0x02, 0xFF],

                }
            )))
        ui.dr.clicked.connect(
            lambda: cam.send_command(cam.commands["move"].get_command(
                {
                    "speed": ui.speed_bar.value() / 100,
                    "postfix": [0x02, 0x02, 0xFF],

                }
            )))

        ui.focus_far.clicked.connect(
            lambda: cam.send_command(cam.commands["focus far var"].get_command(
                {
                    "speed": ui.focus_speed.value() / 100,
                }
            )))
        ui.focus_near.clicked.connect(
            lambda: cam.send_command(cam.commands["focus near var"].get_command(
                {
                    "speed": ui.focus_speed.value() / 100,
                }
            )))
        ui.focus_stop.clicked.connect(lambda: cam.send_command(cam.commands["focus stop"].get_command(None)))
        ui.trigger_af.clicked.connect(
            lambda: cam.send_command(cam.commands["af mode one push trigger"].get_command(None)))

        ui.tele_var.clicked.connect(
            lambda: cam.send_command(cam.commands["zoom tele var"].get_command(
                {
                    "speed": ui.zoom_bar.value() / 100,
                }
            )))
        ui.wide_var.clicked.connect(
            lambda: cam.send_command(cam.commands["zoom wide var"].get_command(
                {
                    "speed": ui.zoom_bar.value() / 100,
                }
            )))
        ui.zoom_stop.clicked.connect(lambda: cam.send_command(cam.commands["zoom stop"].get_command(None)))
        ui.tele_std.clicked.connect(lambda: cam.send_command(cam.commands["zoom tele std"].get_command(None)))
        ui.wide_std.clicked.connect(lambda: cam.send_command(cam.commands["zoom wide std"].get_command(None)))

        ui.home.clicked.connect(lambda: cam.send_command(cam.commands["home"].get_command(None)))
        ui.stop.clicked.connect(lambda: cam.send_command(cam.commands["stop"].get_command(None)))

        ui.speed_plus.clicked.connect(lambda: ui.speed_bar.setValue(ui.speed_bar.value() + 1))
        ui.speed_minus.clicked.connect(lambda: ui.speed_bar.setValue(ui.speed_bar.value() - 1))
        ui.speed_reset.clicked.connect(lambda: ui.speed_bar.setValue(50))

        ui.show()

        sys.exit(self.exec_())


if __name__ == '__main__':
    # app = QApplication(sys.argv)
    ex = App()
    ex.main()
