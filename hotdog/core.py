import dataclasses
import dataclasses as dcs
import logging
import pathlib
import subprocess
import threading
import time
import typing
import warnings
from dataclasses import dataclass
from datetime import datetime

import bluesky.utils as bus
import dacite
import fire
import intake.source.utils as isu
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from bluesky.callbacks.core import get_obj_fields, make_class_safe, LiveTable
from bluesky.callbacks.mpl_plotting import LivePlot, LiveScatter
from bluesky.callbacks.mpl_plotting import QtAwareCallback
from bluesky.callbacks.stream import LiveDispatcher
from bluesky.callbacks.zmq import RemoteDispatcher, Publisher, Proxy
from event_model import DocumentNames
from numpy.polynomial import polynomial as P
from scipy.optimize import fsolve
from suitcase.csv import Serializer
from watchdog.events import PatternMatchingEventHandler, FileCreatedEvent
from watchdog.observers import Observer

from hotdog.vend import install_qt_kicker

logger = logging.getLogger(__name__)


@dataclass
class ObserverConfig:
    # TODO: fill in
    watch_path: str = None
    patterns: typing.Union[None, typing.List[str]] = None
    ignore_patterns: typing.Union[None, typing.List[str]] = None
    recursive: bool = True
    timeout: typing.Union[None, float] = None


@dataclass
class ProcessorConfig:
    mode: int = 0
    T0: float = 273.15
    V0: float = 0.
    RT: float = 293.
    prev_csv: typing.Union[None, str] = None
    a_coeffs: typing.List = None
    b_coeffs: typing.List = None
    c_coeffs: typing.List = None
    n_coeffs: int = 3
    tc_path: str = None
    inp_path: str = None
    working_dir: str = None
    xy_file_fmt: str = None
    data_keys: typing.List = None
    metadata: typing.Dict[str, float] = None
    n_scan: int = 1


@dataclass
class ProxyConfig:
    host: str = "localhost"
    in_port: int = 5567
    out_port: int = 5568


@dataclass
class Config:
    observer: ObserverConfig
    processor: ProcessorConfig
    proxy: ProxyConfig


@dataclass
class FitResult:
    Rwp: float
    Vol: float
    tth: np.ndarray
    I: np.ndarray
    Icalc: np.ndarray
    Idiff: np.ndarray


@dataclass
class CalibResult:
    alpha: float
    realVol: float
    T: float


@dataclass
class Result:
    fit: FitResult
    calib: CalibResult


class ProcessorError(Exception):
    pass


class Handler(PatternMatchingEventHandler):

    def __init__(self, config: Config):
        super(Handler, self).__init__(config.observer.patterns, config.observer.ignore_patterns)
        self.config = config
        self.processor = Processor(config.processor)

    def on_any_event(self, event):
        if isinstance(event, FileCreatedEvent):
            self.processor.process_a_file(str(event.src_path))


class Server(Observer):
    """Monitor the directory and let Processor process the newly created files."""

    def __init__(self, config: Config):
        super(Server, self).__init__()
        self.config = config
        self.handler = Handler(config)
        self.processor = self.handler.processor
        self.publisher = Publisher((config.proxy.host, config.proxy.in_port))
        self.processor.subscribe(self.publisher)
        self.schedule(self.handler, path=config.observer.watch_path, recursive=config.observer.recursive)

    def run_until_timeout(self):
        timeout = self.config.observer.timeout
        if timeout is None:
            timeout = float("inf")
        self.start()
        t0 = time.time()
        try:
            while time.time() - t0 <= timeout and not self.handler.processor.stopped:
                time.sleep(1.)
            self.stop()
        except KeyboardInterrupt:
            self.stop()
        self.join()

    @classmethod
    def from_dict(cls, config_dct: dict):
        config = dacite.from_dict(Config, config_dct)
        return cls(config)

    @classmethod
    def from_file(cls, config_file: str):
        with pathlib.Path(config_file).open("r") as f:
            dct = yaml.safe_load(f)
        return cls.from_dict(dct)

    def dump_config(self, config_file: str) -> None:
        dct = dcs.asdict(self.config)
        with pathlib.Path(config_file).open("w") as f:
            yaml.safe_dump(dct, f)
        return


class Processor(LiveDispatcher):
    """Process the data file and publish the results in an event stream."""

    def __init__(self, config: ProcessorConfig):
        super(Processor, self).__init__()
        self.config = config
        self.prev_df = None
        self.load_prev_df()
        self.inp_template = pathlib.Path(self.config.inp_path).read_text()
        self.working_dir = pathlib.Path(self.config.working_dir)
        self.saving_dir = self.working_dir.joinpath("topas_output_files")
        self.tc_path = pathlib.Path(self.config.tc_path)
        self.desc_uid = ""
        self.count = 0
        self.fields = [f.name for f in dataclasses.fields(CalibResult)]
        self.prev_out_file: typing.Union[None, pathlib.Path] = None
        self.prev_xy_file: typing.Union[None, pathlib.Path] = None
        self.prev_res_file: typing.Union[None, pathlib.Path] = None
        self.prev_fit_file: typing.Union[None, pathlib.Path] = None
        coeffs = P.polymul(
            P.polymul(self.config.a_coeffs, self.config.b_coeffs), self.config.c_coeffs
        )[:self.config.n_coeffs]
        self.poly = P.Polynomial(coeffs, domain=[-1e5, 1e5], window=[-1e5, 1e5])
        self.stopped = False
        self.original_time = None
        self.csv_serializer = Serializer(str(self.working_dir), "{start[uid]}_summary_")
        self.subscribe(self.csv_serializer)
        self.print("Processor is ready. The data will be output in {}.".format(str(self.working_dir)))

    def load_prev_df(self):
        if self.config.mode == 0:
            if self.config.prev_csv:
                self.print(
                    "WARNING: This is the first run. "
                    "The previous csv file is not need."
                )
        elif self.config.mode == 1:
            if not self.config.prev_csv:
                self.print(
                    "WARNING: Missing the data from the previous run. "
                    "Use alpha = 1., T = {} at room temperature.".format(self.config.RT)
                )
            else:
                self.prev_df = pd.read_csv(self.config.prev_csv)
        elif self.config.mode == 2:
            if not self.config.prev_csv:
                self.print(
                    "WARNING: Missing the data from the previous run. "
                    "The volume correction will be skipped. "
                    "The T0 and V0 in configuration file will be used."
                )
            else:
                self.prev_df = pd.read_csv(self.config.prev_csv)
        return

    def process_a_file(self, filename: str) -> None:
        """Process the XRD data file and output the documents of the results.

        The fitted data file and result csv file will be generated in the process.

        Parameters
        ----------
        filename : str
            The path to the XRD data file.
        """
        _filename = pathlib.Path(filename)
        self.original_time = _filename.lstat().st_mtime
        self.print("Start processing {}.".format(_filename.name))
        # count
        self.count += 1
        # emit start if this is the first file
        if self.count <= 1:
            self.emit_start(
                {
                    "plan_type": "generator",
                    'plan_name': "hotdog calibration",
                    "scan_id": 1
                }
            )
            self.emit_descriptor()
            self.create_dir()
        # process file
        data = dict()
        raw_data = self.parse_filename(filename)
        data.update(raw_data)
        fr = self.run_topas(filename)
        data.update(dcs.asdict(fr))
        cr = self.run_calib(fr, raw_data)
        data.update(dcs.asdict(cr))
        # emit event data
        try:
            self.process_event({"data": data, "descriptor": self.desc_uid})
        except Exception as error:
            self.emit_stop("fail")
            self.count = 0
            raise error
        # emit stop if this is the last file
        if self.count >= self.config.n_scan:
            self.emit_stop("success")
            self.count = 0
        return

    def create_dir(self):
        self.working_dir.mkdir(exist_ok=True)
        self.saving_dir.mkdir(exist_ok=True)

    def process_many_files(self, filenames: typing.Iterable[str]) -> None:
        for f in filenames:
            self.process_a_file(f)
        return

    def emit(self, name, doc):
        doc["time"] = self.original_time
        return super(Processor, self).emit(name, doc)

    def run_topas(self, filename: str) -> FitResult:
        wd = self.saving_dir
        tc_path = self.tc_path
        # get all file paths
        xy_file = pathlib.Path(filename)
        out_fp = wd.joinpath(xy_file.stem)
        inp_file = out_fp.with_suffix(".inp")
        res_file = out_fp.with_suffix(".res")
        fit_file = out_fp.with_suffix(".fit")
        out_file = out_fp.with_suffix(".out")
        # write out the inp file
        if self.count <= 1:
            inp_text = self.inp_template
            inp_text = inp_text.replace(
                "xy_file", str(xy_file)
            ).replace(
                "res_file", str(res_file)
            ).replace(
                "fit_file", str(fit_file)
            )
        elif self.prev_out_file is not None and self.prev_out_file.is_file():
            inp_text = self.prev_out_file.read_text()
            inp_text = inp_text.replace(
                str(self.prev_xy_file), str(xy_file)
            ).replace(
                str(self.prev_res_file), str(res_file)
            ).replace(
                str(self.prev_fit_file), str(fit_file)
            )
        else:
            raise ProcessorError("The previous output file is not saved.")
        inp_file.touch()
        inp_file.write_text(inp_text)
        # run topas on this file
        cmd = [str(tc_path), str(inp_file)]
        cp = subprocess.run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # if run fails, raise error
        if cp.returncode != 0:
            raise ProcessorError(cp.stderr.decode())
        # read result
        if not res_file.is_file():
            raise ProcessorError(
                "{} doesn't exist. Below is the output from TOPAS:\n{}".format(
                    str(res_file),
                    cp.stdout.decode()
                )
            )
        res = np.loadtxt(str(res_file), delimiter=",", dtype=float)
        if not (res.ndim == 1 and res.shape[0] == 2):
            raise ProcessorError(
                "The {} is not one-row and two-column file.".format(str(res_file))
            )
        # read fit
        if not fit_file.is_file():
            raise ProcessorError(
                "{} doesn't exist. Below is the output from TOPAS:\n{}".format(
                    str(fit_file),
                    cp.stdout.decode()
                )
            )
        fit = np.loadtxt(str(fit_file), delimiter=",", dtype=float).transpose()
        if not (fit.ndim == 2 and fit.shape[0] == 4):
            raise ProcessorError("The {} is not four-column file.".format(str(fit_file)))
        # update previous file path
        self.prev_out_file = out_file
        self.prev_xy_file = xy_file
        self.prev_fit_file = fit_file
        self.prev_res_file = res_file
        return FitResult(Rwp=res[0], Vol=res[1], tth=fit[0], I=fit[1], Icalc=fit[2], Idiff=fit[3])

    def run_calib(self, fitresult: FitResult, raw_data: dict) -> CalibResult:
        mode = self.config.mode
        if mode == 0:
            # Record the V0, T0
            return self.run_calib_0(fitresult)
        elif mode == 1:
            # Calculate the correction parameter
            return self.run_calib_1(fitresult)
        elif mode == 2:
            # Calculate the real volume and temperature
            return self.run_calib_2(fitresult, raw_data)
        else:
            raise ValueError("Unknown mode: {}. Require mode in 0, 1, 2.".format(mode))

    def run_calib_0(self, fitresult: FitResult) -> CalibResult:
        return CalibResult(1., fitresult.Vol, self.config.RT)

    def run_calib_1(self, fitresult: FitResult) -> CalibResult:
        if self.prev_df is None:
            realVol = fitresult.Vol
            alpha = 1
            T = self.config.RT
        else:
            realVol = self.prev_df["realVol"][0]
            alpha = realVol / fitresult.Vol
            T = self.prev_df["T"][0]
        return CalibResult(alpha, realVol, T)

    def run_calib_2(self, fitresult: FitResult, raw_data: dict) -> CalibResult:
        T0 = self.config.T0
        if self.prev_df is None:
            V0 = self.config.V0
            realVol = fitresult.Vol
            alpha = 1.
        else:
            cr0 = self._get_prev_result(raw_data)
            alpha = cr0.alpha
            realVol = alpha * fitresult.Vol
            c = self.poly(np.array([cr0.T - T0]))[0]
            V0 = cr0.realVol / c

        def func(x):
            return V0 * self.poly(x) - realVol

        T = fsolve(func, np.array([1000.]), xtol=1e-4)[0] + T0
        return CalibResult(alpha, realVol, T)

    def _get_prev_result(self, raw_data: dict) -> CalibResult:
        keys = list(raw_data.keys())
        sel_prev_df: pd.DataFrame = self.prev_df[keys]
        raw_sr = pd.Series(raw_data)
        dist = (sel_prev_df - raw_sr).pow(2).sum(axis=1)
        idx = dist.argmin()
        if idx < 0 or idx > sel_prev_df.shape[0] - 1:
            raise ProcessorError("Cannot find the closest value in previous dataframe.")
        row = self.prev_df.iloc[idx][self.fields]
        return CalibResult(**row.to_dict())

    @staticmethod
    def print(message: str):
        now = datetime.now()
        dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
        text = "[{}] {}".format(dt_string, message)
        return print(text)

    def emit_start(self, meta: dict) -> str:
        user_meta = self.config.metadata
        dks = self.config.data_keys
        if user_meta is None:
            user_meta = {}
        uid = bus.new_uid()
        doc = dict(**meta, **user_meta)
        doc["uid"] = uid
        doc["hints"] = {'dimensions': [([dk], 'primary') for dk in dks]}
        self.start(doc)
        return uid

    def parse_filename(self, filename: str) -> dict:
        xy_file_fmt = self.config.xy_file_fmt
        data_keys = self.config.data_keys
        # parse file name
        xy_file = pathlib.Path(filename)
        dct = isu.reverse_format(xy_file_fmt, xy_file.name)
        # split it to data and metadata
        data = dict()
        for key, val in dct.items():
            if key in data_keys:
                val: str
                data[key] = float(val.replace(",", "."))
        return data

    def emit_descriptor(self) -> str:
        uid = bus.new_uid()
        self.descriptor({"uid": uid, "data_keys": {}, "name": "primary"})
        self.desc_uid = uid
        return uid

    def emit_stop(self, exit_status: str) -> str:
        uid = bus.new_uid()
        self.stop({"uid": uid, "exit_status": exit_status})
        self.stopped = True
        return uid


class VisServer(RemoteDispatcher):

    def __init__(self, config: Config):
        self.config = config
        super(VisServer, self).__init__((self.config.proxy.host, self.config.proxy.out_port))
        self.fitplot = FitPlot("I", "Icalc", "tth")
        self.subscribe(self.fitplot)
        self.livetable = LiveTable(self.config.processor.data_keys + ["Vol", "alpha", "realVol", "T"])
        self.subscribe(self.livetable)
        self.livescatter = None
        self.liveplot = None
        data_keys = self.config.processor.data_keys
        if len(data_keys) == 2:
            self.livescatter = LiveScatter(*data_keys, "T")
            self.subscribe(self.livescatter)
        elif len(data_keys) == 1:
            self.liveplot = LivePlot("T", data_keys[0])
            self.subscribe(self.liveplot)
        else:
            self.liveplot = LivePlot("T")
            self.subscribe(self.liveplot)
        install_qt_kicker(self.loop)

    @classmethod
    def from_dict(cls, config_dct: dict):
        config = dacite.from_dict(Config, config_dct)
        return cls(config)

    @classmethod
    def from_file(cls, config_file: str):
        with pathlib.Path(config_file).open("r") as f:
            dct = yaml.safe_load(f)
        return cls.from_dict(dct)

    def dump_config(self, config_file: str) -> None:
        dct = dcs.asdict(self.config)
        with pathlib.Path(config_file).open("w") as f:
            yaml.safe_dump(dct, f)
        return


@make_class_safe(logger=logger)
class FitPlot(QtAwareCallback):
    def __init__(self, y, ycalc, x=None, *, offset: float = 0., legend_keys=None, xlim=None, ylim=None,
                 ax=None, fig=None, epoch='run', **kwargs):
        super().__init__(use_teleporter=kwargs.pop('use_teleporter', None))
        self.__setup_lock = threading.Lock()
        self.__setup_event = threading.Event()

        def setup():
            # Run this code in start() so that it runs on the correct thread.
            nonlocal y, ycalc, x, offset, legend_keys, xlim, ylim, ax, fig, epoch, kwargs
            import matplotlib.pyplot as plt
            with self.__setup_lock:
                if self.__setup_event.is_set():
                    return
                self.__setup_event.set()
            if fig is not None:
                if ax is not None:
                    raise ValueError("Values were given for both `fig` and `ax`. "
                                     "Only one can be used; prefer ax.")
                warnings.warn("The `fig` keyword arugment of LivePlot is "
                              "deprecated and will be removed in the future. "
                              "Instead, use the new keyword argument `ax` to "
                              "provide specific Axes to plot on.")
                ax = fig.gca()
            if ax is None:
                fig, ax = plt.subplots()
            self.ax = ax

            if legend_keys is None:
                legend_keys = []
            self.legend_keys = legend_keys
            if x is not None:
                self.x, *others = get_obj_fields([x])
            else:
                self.x = 'seq_num'
            self.y, *others = get_obj_fields([y])
            self.ycalc, *others = get_obj_fields([ycalc])
            self.ax.set_ylabel(y)
            self.ax.set_xlabel(x or 'sequence #')
            if xlim is not None:
                self.ax.set_xlim(*xlim)
            if ylim is not None:
                self.ax.set_ylim(*ylim)
            self.ax.margins(.1)
            self.kwargs = kwargs
            self.legend = None
            self.legend_title = " :: ".join([name for name in self.legend_keys])
            self._epoch_offset = None  # used if x == 'time'
            self._epoch = epoch
            self.offset = offset
            self.x_data = []
            self.y_data = []
            self.ycalc_data = []
            self.ydiff_data = []

        self.__setup = setup

    def start(self, doc):
        self.__setup()
        # The doc is not used; we just use the signal that a new run began.
        self._epoch_offset = doc['time']  # used if self.x == 'time'
        kwargs = self.kwargs
        self.current_data_line, = self.ax.plot([], [], label="data", **kwargs)
        self.current_fit_line, = self.ax.plot([], [], label="fit", **kwargs)
        self.current_diff_line, = self.ax.plot([], [], label="diff", **kwargs)
        legend = self.ax.legend(loc=0, title=self.legend_title)
        try:
            # matplotlib v3.x
            self.legend = legend.set_draggable(True)
        except AttributeError:
            # matplotlib v2.x (warns in 3.x)
            self.legend = legend.draggable(True)
        super().start(doc)

    def event(self, doc):
        "Unpack data from the event and call self.update()."
        # This outer try/except block is needed because multiple event
        # streams will be emitted by the RunEngine and not all event
        # streams will have the keys we want.
        try:
            # This inner try/except block handles seq_num and time, which could
            # be keys in the data or accessing the standard entries in every
            # event.
            try:
                new_x = doc['data'][self.x]
            except KeyError:
                if self.x in ('time', 'seq_num'):
                    new_x = doc[self.x]
                else:
                    raise
            new_y = doc['data'][self.y]
            new_ycalc = doc['data'][self.ycalc]
        except KeyError:
            # wrong event stream, skip it
            return

        # Special-case 'time' to plot against against experiment epoch, not
        # UNIX epoch.
        if self.x == 'time' and self._epoch == 'run':
            new_x -= self._epoch_offset

        self.update_caches(new_x, new_y, new_ycalc)
        self.update_plot()
        super().event(doc)

    def update_caches(self, x, y, ycalc):
        self.y_data = y
        self.x_data = x
        self.ycalc_data = ycalc
        self.ydiff_data = y - ycalc

    def update_plot(self):
        self.current_data_line.set_data(self.x_data, self.y_data)
        self.current_fit_line.set_data(self.x_data, self.ycalc_data)
        self.current_diff_line.set_data(self.x_data, self.ydiff_data + self.offset)
        # Rescale and redraw.
        self.ax.relim(visible_only=True)
        self.ax.autoscale_view(tight=True)
        self.ax.figure.canvas.draw()
        plt.pause(0.01)

    def stop(self, doc):
        if len(self.x_data) == 0:
            print('LivePlot did not get any data that corresponds to the '
                  'x axis. {}'.format(self.x))
        if len(self.y_data) == 0:
            print('LivePlot did not get any data that corresponds to the '
                  'y axis. {}'.format(self.y))
        if len(self.ycalc_data) == 0:
            print('LivePlot did not get any data that corresponds to the '
                  'ycalc axis. {}'.format(self.ycalc))
        if len(self.y_data) != len(self.x_data):
            print('LivePlot has a different number of elements for x ({}) and'
                  'y ({})'.format(len(self.x_data), len(self.y_data)))
        if len(self.ycalc_data) != len(self.x_data):
            print('LivePlot has a different number of elements for x ({}) and'
                  'ycalc ({})'.format(len(self.x_data), len(self.ycalc_data)))
        super().stop(doc)


class MyProxy(Proxy):

    def __init__(self, config: Config):
        self.config = config.proxy
        super(MyProxy, self).__init__(self.config.in_port, self.config.out_port)

    @classmethod
    def from_dict(cls, config_dct: dict):
        config = dacite.from_dict(Config, config_dct)
        return cls(config)

    @classmethod
    def from_file(cls, config_file: str):
        with pathlib.Path(config_file).open("r") as f:
            dct = yaml.safe_load(f)
        return cls.from_dict(dct)

    def dump_config(self, config_file: str) -> None:
        dct = dcs.asdict(self.config)
        with pathlib.Path(config_file).open("w") as f:
            yaml.safe_dump(dct, f)
        return


def run_hotdog(config_file: str) -> None:
    """Start a HotDog server using the configuration file."""
    server = Server.from_file(config_file)
    server.run_until_timeout()
    return


def hotdog() -> None:
    fire.Fire(run_hotdog)
    return


def run_hotdogvis(config_file: str) -> None:
    """Start a HotDog visualization server using the configuration file."""
    server = VisServer.from_file(config_file)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            server.start()
        while True:
            time.sleep(1.)
    except KeyboardInterrupt:
        server.closed = True
    return


def hotdogvis() -> None:
    fire.Fire(run_hotdogvis)
    return


def run_hotdogproxy(config_file: str) -> None:
    """Start a HotDog proxy server using the configuration file."""
    server = MyProxy.from_file(config_file)
    try:
        server.start()
        while True:
            time.sleep(1.)
    except KeyboardInterrupt:
        server.closed = True
    return


def hotdogproxy() -> None:
    fire.Fire(run_hotdogproxy)
    return
