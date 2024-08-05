from qtpy import QtWidgets
from pymodaq.control_modules.viewer_utility_classes import DAQ_Viewer_base, comon_parameters, main
import numpy as np
from collections import OrderedDict
from pymodaq.utils.daq_utils import ThreadCommand, getLineInfo
from pymodaq.utils.data import DataFromPlugins, Axis, DataToExport
import sys
import time
from msl.equipment import EquipmentRecord, ConnectionRecord, Backend
from _ctypes import Structure
from ctypes import CDLL, c_int, create_string_buffer, byref, c_ulong, c_char
import os

def get_spectrometers_list(dll_path):
    dll = CDLL(dll_path)

    try:
        # Initialiser le SDK pour USB uniquement

        result = dll.AVS_Init(c_int(0))  # 0 pour utiliser le port USB uniquement
        if result <= 0:
            print(f"Error initializing SDK: {result}")
            return []

        # Mettre à jour les périphériques USB
        usb_result = dll.AVS_UpdateUSBDevices()

        # Déterminer la taille du buffer nécessaire pour obtenir la liste des périphériques

        list_size = c_ulong(0)
        result = dll.AVS_GetList(c_ulong(0), byref(list_size), None)
        if result != 0:
            # Allouer le buffer pour la liste des périphériques
            buffer_size = list_size.value
            buffer = create_string_buffer(buffer_size)

            # Obtenir la liste des périphériques
            result = dll.AVS_GetList(c_ulong(buffer_size), byref(list_size), buffer)
            if result != 0:
                # Traiter la liste des périphériques
                raw_data = buffer.raw
                # Extraire les numéros de série
                spectrometers = []
                index = 0
                while index < len(raw_data):
                    # Extraire le numéro de série
                    end_index = raw_data.find(b'U1', index)
                    if end_index == -1:
                        break
                    serial_number = raw_data[index:end_index + 2].decode('utf-8', errors='ignore').strip('\x00')
                    if serial_number:
                        print(f"Spectrometer found with Serial Number: {serial_number}")
                        spectrometers.append(serial_number)
                    index = end_index + 2  # Passer à l'élément suivant

                return spectrometers
            else:
                print("No devices found or error in getting the device list.")
        else:
            print("Error in determining buffer size.")

    except Exception as e:
        print(f"Exception occurred: {e}")

    finally:
        # Désactiver le SDK
        dll.AVS_Done()

    return []
class DAQ_1DViewer_AvaSpec(DAQ_Viewer_base):
    """PyMoDAQ plugin controlling AvaSpec-2048L spectrometers using the Avantes SDK"""

    avaspec_dll_path = 'C:\\AvaSpecX64-DLL_9.14.0.0\\avaspecx64.dll'  # Update this path if necessary
    spectro_names = get_spectrometers_list(avaspec_dll_path)
    avaspec_serial = spectro_names[0]  # Update this serial number if necessary
    params = comon_parameters + [
        {'title': 'Avantes DLL path:', 'name': 'avaspec_dll_path', 'type': 'browsepath', 'value': avaspec_dll_path},
        {'title': 'Avantes Serial:', 'name': 'avaspec_serial', 'type': 'str', 'value': avaspec_serial},
        {'title': 'N spectrometers:', 'name': 'Nspectrometers', 'type': 'int', 'value': 0, 'default': 0, 'min': 0},
        {'title': 'Spectrometers:', 'name': 'spectrometers', 'type': 'group', 'children': []},
    ]

    def ini_attributes(self):
        self.controller = None
        self.spectro_names = []  # contains the spectro name as returned from the wrapper
        self.spectro_id = []  # contains the spectro id as set by the ini_detector method and equal to the Parameter name

    def commit_settings(self, param):
        if param.name() == 'exposure_time':
            ind_spectro = self.spectro_id.index(param.parent().name())
            cfg = self.controller.MeasConfigType()
            cfg.m_IntegrationTime = param.value()
            self.controller.prepare_measure(cfg)
            param.setValue(cfg.m_IntegrationTime)
        elif param.name() == 'avaspec_dll_path' or param.name() == 'avaspec_serial':
            self.initialize_controller(param.value(), self.settings.child('avaspec_serial').value())

    def ini_detector(self, controller=None):
        if self.settings['controller_status'] == "Slave":
            if controller is None:
                raise Exception('No controller has been defined externally while this axe is a slave one')
            else:
                self.controller = controller
        else:  # Master stage
            self.initialize_controller(self.settings.child('avaspec_dll_path').value(),
                                       self.settings.child('avaspec_serial').value())
            if self.controller is None:
                return '', False

            try:
                self.spectro_names = ['AvaSpec-2048L']  # Update this if necessary
                self.spectro_id = ['spectro0']

                num_pixels = self.controller.get_num_pixels()
                wavelengths = self.controller.get_lambda()
                data_init = DataToExport('Spectro')
                data_init.append(DataFromPlugins(name=self.spectro_names[0], data=[np.zeros(num_pixels)], dim='Data1D',
                                                 axes=[Axis(data=wavelengths, label='Wavelength', units='nm')]))

                self.settings.child('Nspectrometers').setValue(1)
                self.settings.child('spectrometers').addChild(
                    {'title': self.spectro_names[0], 'name': 'spectro0', 'type': 'group', 'children': [
                        {'title': 'grab spectrum:', 'name': 'grab', 'type': 'bool', 'value': True},
                        {'title': 'Exposure time (ms):', 'name': 'exposure_time', 'type': 'int',
                         'value': 5, 'min': 1, 'max': 10000},
                    ]
                     })
                self.dte_signal_temp.emit(data_init)
            except Exception as e:
                print(f"Failed to initialize spectrometer: {e}")
                return '', False

        initialized = True
        info = 'Detector initialized successfully'
        return info, initialized

    def initialize_controller(self, dll_path, serial_number):
        try:
            record = EquipmentRecord(
                manufacturer='Avantes',
                model='AvaSpec-2048L',
                serial=serial_number,
                connection=ConnectionRecord(
                    address=f'SDK::{dll_path}',
                    backend=Backend.MSL,
                )
            )
            self.controller = record.connect()
            print('Connected to AvaSpec-2048L')
        except Exception as e:
            print(f"Failed to connect to AvaSpec-2048L: {e}")
            self.controller = None

    def get_xaxis(self, ind_spectro):
        try:
            wavelengths = self.controller.get_lambda()
            return wavelengths
        except Exception as e:
            print(f"Failed to get wavelengths for spectrometer {ind_spectro}: {e}")
            return np.array([])

    def close(self):
        if self.controller is not None:
            self.controller.disconnect()

    def grab_data(self, Naverage=1, **kwargs):

        dte = DataToExport('Spectro')

        for ind_spectro in range(len(self.spectro_names)):
            grab_param = self.settings.child('spectrometers', 'spectro0', 'grab').value()
            exposure_time = self.settings.child('spectrometers', 'spectro0', 'exposure_time').value()

            if grab_param:

                meas_cfg = self.controller.MeasConfigType()
                meas_cfg.m_IntegrationTime = exposure_time
                meas_cfg.m_NrAverages = Naverage
                meas_cfg.m_StopPixel = self.controller.get_num_pixels() - 1
                self.controller.prepare_measure(meas_cfg)
                self.controller.measure(1)
                start_time = time.time()
                while not self.controller.poll_scan():
                    time.sleep(0.01)
                    if time.time() - start_time > 10:

                        break

                tick_count, data = self.controller.get_data()
                data_array = np.array(data)
                dte.append(DataFromPlugins(name=self.spectro_names[ind_spectro], data=[data_array], dim='Data1D'))

            QtWidgets.QApplication.processEvents()

        self.dte_signal.emit(dte)


    def stop(self):
        # No specific stop function provided in example script, assuming stopAveraging is not required for AvaSpec
        pass






if __name__ == '__main__':
    main(__file__)
