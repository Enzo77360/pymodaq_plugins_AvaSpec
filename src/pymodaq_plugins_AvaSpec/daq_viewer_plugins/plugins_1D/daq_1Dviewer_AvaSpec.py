from qtpy import QtWidgets
from pymodaq.control_modules.viewer_utility_classes import DAQ_Viewer_base, comon_parameters, main
import numpy as np
from collections import OrderedDict
from pymodaq.utils.daq_utils import ThreadCommand, getLineInfo
from pymodaq.utils.data import DataFromPlugins, Axis, DataToExport
import sys
import time
from msl.equipment import EquipmentRecord, ConnectionRecord, Backend


class DAQ_1DViewer_AvaSpec(DAQ_Viewer_base):
    """PyMoDAQ plugin controlling AvaSpec-2048L spectrometers using the Avantes SDK"""

    avaspec_dll_path = 'C:\\AvaSpecX64-DLL_9.14.0.0\\avaspecx64.dll'  # Update this path if necessary
    avaspec_serial = '2206034U1'  # Update this serial number if necessary
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
        print("Starting data acquisition...")

        dte = DataToExport('Spectro')

        for ind_spectro in range(len(self.spectro_names)):
            grab_param = self.settings.child('spectrometers', 'spectro0', 'grab').value()
            exposure_time = self.settings.child('spectrometers', 'spectro0', 'exposure_time').value()

            if grab_param:
                print(f"Preparing measurement for {self.spectro_names[ind_spectro]}")

                meas_cfg = self.controller.MeasConfigType()
                meas_cfg.m_IntegrationTime = exposure_time
                meas_cfg.m_NrAverages = Naverage
                meas_cfg.m_StopPixel = self.controller.get_num_pixels() - 1
                self.controller.prepare_measure(meas_cfg)

                print(
                    f"Configuration: Integration Time = {meas_cfg.m_IntegrationTime}, Averages = {meas_cfg.m_NrAverages}, Stop Pixel = {meas_cfg.m_StopPixel}")

                self.controller.measure(1)
                print("Measurement started, waiting for completion...")

                start_time = time.time()
                while not self.controller.poll_scan():
                    time.sleep(0.01)
                    if time.time() - start_time > 10:
                        print("Measurement timeout")
                        break

                tick_count, data = self.controller.get_data()
                data_array = np.array(data)

                print(f"Spectrometer: {self.spectro_names[ind_spectro]}")
                print(f"Data (first 10 values): {data_array[:10]}")
                print(f"Data (last 10 values): {data_array[-10:]}")
                print(f"Data length: {len(data_array)}")
                print(f"Tick count: {tick_count}")
                print(f"Dark Pixel data: {self.controller.get_dark_pixel_data()}")

                dte.append(DataFromPlugins(name=self.spectro_names[ind_spectro], data=[data_array], dim='Data1D'))

            QtWidgets.QApplication.processEvents()

        self.dte_signal.emit(dte)
        print("Data emitted to signal")

    def stop(self):
        # No specific stop function provided in example script, assuming stopAveraging is not required for AvaSpec
        pass


if __name__ == '__main__':
    main(__file__)
