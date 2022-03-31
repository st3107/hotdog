import collections
import dataclasses
import dataclasses as dcs
import itertools as it
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
import tqdm
import yaml
from bluesky.callbacks.core import LiveTable, get_obj_fields, make_class_safe
from bluesky.callbacks.mpl_plotting import (LivePlot, LiveScatter,
                                            QtAwareCallback)
from bluesky.callbacks.stream import LiveDispatcher
from bluesky.callbacks.zmq import Proxy, Publisher, RemoteDispatcher
from numpy.polynomial import polynomial as P
from scipy.optimize import fsolve
from watchdog.events import FileCreatedEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

from hotdog.vend import install_qt_kicker

# logger for the plotting functions
logger = logging.getLogger(__name__)
# type alias
AnyData = typing.Dict[str, float]
MetaData = typing.Dict[str, typing.Any]
RawData = typing.Dict[str, float]
ParsedData = typing.Tuple[RawData, MetaData]


class ConfigError(Exception):
    pass


@dataclass
class ObserverConfig:
    watch_path: str = None
    patterns: typing.Union[None, typing.List[str]] = None
    ignore_patterns: typing.Union[None, typing.List[str]] = None
    recursive: bool = True
    timeout: typing.Union[None, float, int] = None

    def validate(self) -> None:
        wp = pathlib.Path(self.watch_path)
        if not wp.is_dir():
            raise ConfigError("{} doesn't exist.".format(str(wp)))
        return


@dataclass
class ProcessorConfig:
    mode: int = 0
    T0: float = 273.15
    V0: float = 0.
    RT: float = 293.15
    prev_csv: typing.Union[None, str] = None
    a_coeffs: typing.Union[None, typing.List] = dcs.field(default_factory=lambda: [1.])
    b_coeffs: typing.Union[None, typing.List] = dcs.field(default_factory=lambda: [1.])
    c_coeffs: typing.Union[None, typing.List] = dcs.field(default_factory=lambda: [1.])
    n_coeffs: int = 3
    tc_path: str = None
    sequential_fit: bool = False
    inp_path: str = None
    working_dir: str = None
    xy_file_fmt: str = None
    data_keys: typing.List = None
    metadata: typing.Dict[str, float] = None
    tolerance: float = 1e-4  # in what range two points are considered the same
    is_test: bool = False
    progress_bar: bool = True

    def validate(self) -> None:
        if not (0 <= self.mode <= 2):
            raise ConfigError("The `mode` can only be 0, 1, 2. This is {}.".format(self.mode))
        if self.V0 < 0.:
            raise ConfigError("The `V0` can not be negative. This is {}.".format(self.V0))
        if not self.prev_csv:
            raise ConfigError("The `prev_csv` should be provided.")
        if not self.a_coeffs:
            raise ConfigError("The `a_coeffs` should at least contain zero order term.")
        if not self.b_coeffs:
            raise ConfigError("The `b_coeffs` should at least contain zero order term.")
        if not self.c_coeffs:
            raise ConfigError("The `c_coeffs` should at least contain zero order term.")
        if self.n_coeffs <= 0:
            raise ConfigError("The `n_coeffs` must be positive number.")
        order = len(self.a_coeffs) + len(self.b_coeffs) + len(self.c_coeffs) - 3
        if self.n_coeffs >= order:
            raise ConfigError(
                "The `n_coeffs` cannot be larger than the maximum order in polynomial."
                "This is {} > {}.".format(self.n_coeffs, order)
            )
        for attr in [self.tc_path, self.inp_path]:
            _path = pathlib.Path(attr)
            if not _path.is_file():
                raise ConfigError("{} doesn't exist.".format(str(_path)))
        _path = pathlib.Path(self.working_dir)
        if not _path.is_dir():
            raise ConfigError("{} doesn't exist.".format(str(_path)))
        if "filename" in self.data_keys:
            raise ConfigError("The `filename` is a default data key. Please use another name.")
        if "time" in self.data_keys:
            raise ConfigError("The `time` is a default data key. Please use another name")
        for dk in self.data_keys + ["time"]:
            term = "{" + dk + "}"
            if term not in self.xy_file_fmt:
                raise ConfigError("The data_key `{}` is not in the `xy_file_fmt`.".format(dk))
        return


@dataclass
class ProxyConfig:
    host: str = "localhost"
    in_port: int = 5567
    out_port: int = 5568

    def validate(self) -> None:
        return


@dataclass
class Config:
    observer: ObserverConfig = ObserverConfig()
    processor: ProcessorConfig = ProcessorConfig()
    proxy: ProxyConfig = ProxyConfig()

    def validate(self):
        self.observer.validate()
        self.processor.validate()
        self.proxy.validate()

    @classmethod
    def from_dict(cls, config_dct: dict):
        return dacite.from_dict(cls, config_dct)

    @classmethod
    def from_file(cls, config_file: str):
        with pathlib.Path(config_file).open("r") as f:
            config_dct = yaml.safe_load(f)
        return cls.from_dict(config_dct)

    def to_dict(self) -> dict:
        return dcs.asdict(self)

    def to_yaml(self, yaml_file: str) -> None:
        yaml_file = str(pathlib.PurePath(yaml_file))
        dct = self.to_dict()
        with pathlib.Path(yaml_file).open("w") as f:
            yaml.safe_dump(dct, f)
        return


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
        self.processor = Processor(config)

    def on_any_event(self, event):
        if isinstance(event, FileCreatedEvent):
            self.processor.process_a_file(str(event.src_path))


class MyPublisher(Publisher):

    def __init__(self, config: ProxyConfig):
        super().__init__((config.host, config.in_port))


class Server(Observer):
    """Monitor the directory and let Processor process the newly created files."""

    def __init__(self, config: Config):
        super(Server, self).__init__()
        self.config = config
        self.handler = Handler(config)
        self.processor = self.handler.processor
        self.publisher = MyPublisher(config.proxy)
        self.processor.subscribe(self.publisher)
        self.schedule(self.handler, path=config.observer.watch_path, recursive=config.observer.recursive)
        self.config.validate()
    
    def run_until_timeout(self):
        timeout = self.config.observer.timeout
        if timeout is None:
            timeout = float("inf")
        # when start process the unrecorded files
        self.start()
        t0 = time.time()
        try:
            while ((time.time() - t0) <= timeout) and (not self.handler.processor.stopped):
                time.sleep(1.)
        except KeyboardInterrupt:
            pass
        finally:
            # finalizing up the processing
            self.handler.processor.stop_and_reset()
            # stop the server
            self.stop()
        self.join()

    def start(self):
        self.processor.replay_records()
        self.processor.process_unrecorded_files()
        return super().start()

    @classmethod
    def from_dict(cls, config_dct: dict):
        config = Config.from_dict(config_dct)
        return cls(config)

    @classmethod
    def from_file(cls, config_file: str):
        config = Config.from_file(config_file)
        return cls(config)

    def _dump_config(self, config_file: str) -> None:
        dct = dcs.asdict(self.config)
        with pathlib.Path(config_file).open("w") as f:
            yaml.safe_dump(dct, f)
        return


class Processor(LiveDispatcher):
    """Process the data file and publish the results in an event stream."""

    def __init__(self, config: Config):
        super(Processor, self).__init__()
        self.input_config = config
        self.config = config.processor
        self.obs_config = config.observer
        self.prev_df = self._load_prev_df()
        self.inp_template = pathlib.Path(self.config.inp_path).read_text()
        self.working_dir = pathlib.Path(self.config.working_dir)
        self.saving_dir = self.working_dir.joinpath("topas_output_files")
        self.input_dir = pathlib.Path(self.input_config.observer.watch_path)
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
        self.file_prefix = "{start[uid]}_summary_"
        self._create_dir()
        self._print("Processor is ready. The data will be output in {}.".format(str(self.working_dir)))

    def _load_prev_df(self) -> pd.DataFrame:
        csv_path = pathlib.Path(self.config.prev_csv)
        if not csv_path.is_file():
            df = pd.DataFrame()
            return df
        return pd.read_csv(str(csv_path))

    def _save_data(self, data: AnyData, metadata: MetaData) -> None:
        ignored = {"tth", "I", "Icalc", "Idiff"}
        new_data = {k: v for k, v in data.items() if k not in ignored}
        new_df = pd.Series(dict(**new_data, **metadata)).to_frame().T
        csv_path = pathlib.Path(self.config.prev_csv)
        if self.count == 1 and not csv_path.is_file():
            new_df.to_csv(str(csv_path), mode='w', header=True, index=False)
        else:
            new_df.to_csv(str(csv_path), mode='a', header=False, index=False)
        self.prev_df = pd.concat((self.prev_df, new_df), ignore_index=True, copy=False)
        return

    def process_a_file(self, filename: typing.Union[str, pathlib.Path]) -> None:
        """Process the XRD data file and output the documents of the results.

        The fitted data file and result csv file will be generated in the process.

        Parameters
        ----------
        filename : str
            The path to the XRD data file.
        """
        _filename = pathlib.Path(filename)
        self._print("Process \"{}\".".format(_filename.name))
        # count
        self.count += 1
        # process file
        data = dict()
        raw_data, metadata = self._parse_filename(str(_filename))
        data.update(raw_data)
        fr = self._run_topas(str(_filename))
        data.update(dcs.asdict(fr))
        cr = self._run_calib(fr, raw_data)
        data.update(dcs.asdict(cr))
        # save the data in file and memory
        self._save_data(data, metadata)
        # emit start if this is the first file
        if self.count == 1:
            self._emit_start()
            self._emit_descriptor()
        # emit event data
        self.process_event({"data": data, "descriptor": self.desc_uid})
        return

    def _create_dir(self) -> None:
        self.working_dir.mkdir(exist_ok=True, parents=True)
        self.saving_dir.mkdir(exist_ok=True, parents=True)
        self.input_dir.mkdir(exist_ok=True, parents=True)
        return

    def _process_many_files(self, filenames: typing.Iterable[pathlib.Path]) -> None:
        for f in filenames:
            self.process_a_file(f)
        return

    def _get_file_names(self) -> typing.List[pathlib.Path]:
        _glob = self.input_dir.rglob if self.obs_config.recursive else self.input_dir.glob
        filenames = it.chain(*(_glob(p) for p in self.obs_config.patterns))
        filenames = sorted(filenames, key=self._get_time_str)
        if not filenames:
            raise ProcessorError(
                "Cannot find any files matched to the patterns in '{}'.".format(self.input_dir.absolute())
            )
        return filenames

    def _get_unrecorded_files(self) -> typing.List[pathlib.Path]:
        _glob = self.input_dir.rglob if self.obs_config.recursive else self.input_dir.glob
        filenames = it.chain(*(_glob(p) for p in self.obs_config.patterns))
        recorded = set(self.prev_df["filename"]) if "filename" in self.prev_df.columns else set()
        filenames = (f for f in filenames if f.stem not in recorded)
        return sorted(filenames)

    def process_files_in_dir(self) -> None:
        filenames = self._get_file_names()
        filenames = tqdm.tqdm(filenames, disable=(not self.config.progress_bar))
        self._process_many_files(filenames)
        self.stop_and_reset()
        return

    def process_unrecorded_files(self) -> None:
        filenames = self._get_unrecorded_files()
        self._process_many_files(filenames)
        return

    def emit(self, name, doc):
        if self.original_time is not None:
            doc["time"] = self.original_time
        return super(Processor, self).emit(name, doc)

    def _run_topas(self, filename: str) -> FitResult:
        # if it is a test, return a fake result
        if self.config.is_test:
            a_range = np.array([0., 1.])
            a_zero = np.array([0., 0.])
            a_one = np.array([1., 1.])
            return FitResult(0.0, 1.0, a_range, a_one, a_one, a_zero)
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
        if self.count <= 1 or not self.config.sequential_fit:
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

    def _run_calib(self, fitresult: FitResult, raw_data: RawData) -> CalibResult:
        if self.prev_df.empty:
            # Record the V0, T0
            return self._run_calib_0(fitresult)
        idx = self._get_index(raw_data)
        if idx < 0:
            # Calculate the correction parameter
            return self._run_calib_1(fitresult)
        # Calculate the real volume and temperature
        return self._run_calib_2(fitresult, idx)

    def _run_calib_0(self, fitresult: FitResult) -> CalibResult:
        return CalibResult(1., fitresult.Vol, self.config.RT)

    def _run_calib_1(self, fitresult: FitResult) -> CalibResult:
        if self.prev_df is None:
            realVol = fitresult.Vol
            alpha = 1.
            T = self.config.RT
        else:
            realVol = self.prev_df["realVol"][0]
            alpha = realVol / fitresult.Vol
            T = self.prev_df["T"][0]
        return CalibResult(alpha, realVol, T)

    def _run_calib_2(self, fitresult: FitResult, prev_idx: int) -> CalibResult:
        T0 = self.config.T0
        if self.prev_df is None:
            V0 = self.config.V0
            realVol = fitresult.Vol
            alpha = 1.
        else:
            cr0 = self._get_prev_result(prev_idx)
            alpha = cr0.alpha
            realVol = alpha * fitresult.Vol
            c = self.poly(np.array([cr0.T - T0]))[0]
            V0 = cr0.realVol / c

        def func(x):
            return V0 * self.poly(x) - realVol

        T = fsolve(func, np.array([400.]), xtol=1e-4)[0] + T0
        return CalibResult(alpha, realVol, T)

    def _get_index(self, raw_data: dict) -> int:
        keys = list(raw_data.keys())
        sel_prev_df: pd.DataFrame = self.prev_df[keys]
        raw_sr = pd.Series(raw_data)
        dist = np.sqrt((sel_prev_df - raw_sr).pow(2).sum(axis=1))
        idx = dist.argmin()
        if not (-1 < idx < self.prev_df.shape[0]):
            return -2
        if dist[idx] > self.config.tolerance:
            return -1
        return idx

    def _get_prev_result(self, idx: int) -> CalibResult:
        row = self.prev_df.iloc[idx][self.fields]
        return CalibResult(**row.to_dict())

    @staticmethod
    def _print(message: str):
        now = datetime.now()
        dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
        text = "[{}] {}".format(dt_string, message)
        return print(text)

    def _emit_start(self, meta: typing.Optional[dict] = None) -> None:
        if meta is None:
            meta = {}
        meta.update(
            {
                "plan_type": "generator",
                "plan_name": "hotdog calibration",
                "scan_id": 1
            }
        )
        user_meta = self.config.metadata
        dks = self.config.data_keys
        if user_meta is None:
            user_meta = {}
        uid = bus.new_uid()
        doc = dict(**meta, **user_meta)
        doc["uid"] = uid
        doc["hints"] = {'dimensions': [([dk], 'primary') for dk in dks]}
        return super().start(doc)

    def _get_time_str(self, filename: str):
        xy_file_fmt = self.config.xy_file_fmt
        # parse file name
        xy_file = pathlib.Path(filename)
        dct = isu.reverse_format(xy_file_fmt, xy_file.name)
        return dct["time"]

    def _parse_filename(self, filename: str) -> ParsedData:
        xy_file_fmt = self.config.xy_file_fmt
        data_keys = self.config.data_keys
        # parse file name
        xy_file = pathlib.Path(filename)
        dct = isu.reverse_format(xy_file_fmt, xy_file.name)
        # get the time
        dt = datetime.strptime(dct["time"], "%Y%m%d-%H%M%S")
        self.original_time = time.mktime(dt.timetuple())
        # split it to data and metadata
        data = dict()
        for key, val in dct.items():
            val: str
            if key in data_keys:
                data[key] = float(val.replace(",", "."))
        # get metadata
        metadata = {
            "filename": xy_file.stem,
            "time": dt
        }
        return data, metadata

    def _emit_descriptor(self) -> None:
        uid = bus.new_uid()
        super().descriptor({"uid": uid, "data_keys": {}, "name": "primary"})
        self.desc_uid = uid
        return

    def stop_and_reset(self) -> None:
        if self.count == 0:
            self.stopped = True
            return
        uid = bus.new_uid()
        self.count = 0
        super().stop({"uid": uid, "exit_status": "success"})
        self.stopped = True
        return

    def _dump_next_config_file(self) -> None:
        # dump the config for the next run
        config_dct = dcs.asdict(self.input_config)
        if self.config.mode < 2:
            config_dct["processor"]["mode"] += 1
        prev_df_path = self.working_dir.joinpath(
            "{}primary.csv".format(self.csv_serializer._templated_file_prefix)
        )
        config_dct["processor"]["prev_csv"] = str(prev_df_path)
        next_config_file = prev_df_path.with_suffix(".yaml")
        next_config_file.touch(exist_ok=True)
        with next_config_file.open("w") as f:
            yaml.safe_dump(config_dct, f)
        return

    @classmethod
    def from_dict(cls, config_dct: dict):
        config = dacite.from_dict(Config, config_dct)
        return cls(config)

    @classmethod
    def from_file(cls, config_file: str):
        with pathlib.Path(config_file).open("r") as f:
            dct = yaml.safe_load(f)
        return cls.from_dict(dct)

    def _process_a_row(self, row: typing.NamedTuple) -> None:
        self.count += 1
        data = dict(row._asdict())
        if "filename" in data:
            data.pop("filename")
        if "time" in data:
            t = data.pop("time")
            dt = pd.to_datetime(t)
            self.original_time = time.mktime(dt.timetuple())
        data["tth"] = np.array([0.])
        data["I"] = np.array([0.])
        data["Icalc"] = np.array([0.])
        data["Idiff"] = np.array([0.])
        if self.count == 1:
            self._emit_start()
            self._emit_descriptor()
        self.process_event({"data": data, "descriptor": self.desc_uid})
        return

    def replay_records(self) -> None:
        df = self.prev_df
        if df.empty:
            return
        for row in df.itertuples():
            self._process_a_row(row)
        return


class VisServer(RemoteDispatcher):

    def __init__(self, config: Config):
        self.config = config
        super(VisServer, self).__init__((self.config.proxy.host, self.config.proxy.out_port))
        install_qt_kicker(self.loop)
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
        self.hueplot = HuePlot("T", data_keys, "time", marker="o")
        self.subscribe(self.hueplot)

    @staticmethod
    def print(message: str):
        now = datetime.now()
        dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
        text = "[{}] {}".format(dt_string, message)
        return print(text)

    @classmethod
    def from_dict(cls, config_dct: dict):
        config = Config.from_dict(config_dct)
        return cls(config)

    @classmethod
    def from_file(cls, config_file: str):
        config = Config.from_file(config_file)
        return cls(config)

    def _dump_config(self, config_file: str) -> None:
        dct = dcs.asdict(self.config)
        with pathlib.Path(config_file).open("w") as f:
            yaml.safe_dump(dct, f)
        return

    def start(self):
        self.print("Start visualization server listening to '{}'.".format(self.config.proxy.out_port))
        return super().start()


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
            self.ax.cla()

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
        self.ax.figure.canvas.draw_idle()
        plt.pause(0.01)

    def stop(self, doc):
        if len(self.x_data) == 0:
            print('FitPlot did not get any data that corresponds to the '
                  'x axis. {}'.format(self.x))
        if len(self.y_data) == 0:
            print('FitPlot did not get any data that corresponds to the '
                  'y axis. {}'.format(self.y))
        if len(self.ycalc_data) == 0:
            print('FitPlot did not get any data that corresponds to the '
                  'ycalc axis. {}'.format(self.ycalc))
        if len(self.y_data) != len(self.x_data):
            print('FitPlot has a different number of elements for x ({}) and'
                  'y ({})'.format(len(self.x_data), len(self.y_data)))
        if len(self.ycalc_data) != len(self.x_data):
            print('FitPlot has a different number of elements for x ({}) and'
                  'ycalc ({})'.format(len(self.x_data), len(self.ycalc_data)))
        super().stop(doc)


@make_class_safe(logger=logger)
class HuePlot(QtAwareCallback):
    def __init__(self, y, hue, x=None, *, offset: float = 0., xlim=None, ylim=None,
                 ax=None, fig=None, epoch='run', **kwargs):
        super().__init__(use_teleporter=kwargs.pop('use_teleporter', None))
        self.__setup_lock = threading.Lock()
        self.__setup_event = threading.Event()

        def setup():
            # Run this code in start() so that it runs on the correct thread.
            nonlocal y, hue, x, offset, xlim, ylim, ax, fig, epoch, kwargs
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
            self.ax.cla()

            if x is not None:
                self.x, *others = get_obj_fields([x])
            else:
                self.x = 'seq_num'
            self.y, *others = get_obj_fields([y])
            self.hue = get_obj_fields(hue)
            self.ax.set_ylabel(y)
            self.ax.set_xlabel(x or 'sequence #')
            if xlim is not None:
                self.ax.set_xlim(*xlim)
            if ylim is not None:
                self.ax.set_ylim(*ylim)
            self.ax.margins(.1)
            self.kwargs = kwargs
            self.legend = None
            self.legend_title = ", ".join([name for name in hue])
            self._epoch_offset = None  # used if x == 'time'
            self._epoch = epoch
            self.offset = offset
            self.hue2x = collections.defaultdict(list)
            self.hue2y = collections.defaultdict(list)
            self.hue2line = dict()

        self.__setup = setup

    def start(self, doc):
        self.__setup()
        # The doc is not used; we just use the signal that a new run began.
        self._epoch_offset = doc['time']  # used if self.x == 'time'
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
            new_hue = tuple(doc['data'][k] for k in self.hue)
        except KeyError:
            # wrong event stream, skip it
            return

        # Special-case 'time' to plot against against experiment epoch, not
        # UNIX epoch.
        if self.x == 'time' and self._epoch == 'run':
            new_x -= self._epoch_offset

        self.update_caches(new_x, new_y, new_hue)
        self.update_plot(new_hue)
        super().event(doc)

    def update_caches(self, x, y, hue):
        self.hue2x[hue].append(x)
        self.hue2y[hue].append(y)

    def update_plot(self, hue):
        if hue not in self.hue2line:
            label = ", ".join(["{:.2f}".format(h) for h in hue])
            self.hue2line[hue], = self.ax.plot([], [], label=label, **self.kwargs)
        self.hue2line[hue].set_data(self.hue2x[hue], self.hue2y[hue])
        # Rescale and redraw.
        self.ax.relim(visible_only=True)
        self.ax.autoscale_view(tight=True)
        self.ax.figure.canvas.draw_idle()
        self.legend = self.ax.legend(loc=0, title=self.legend_title)
        plt.pause(0.01)

    def stop(self, doc):
        if len(self.hue2line) == 0:
            print('HuePlot did not get any data.')
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

    @staticmethod
    def print(message: str):
        now = datetime.now()
        dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
        text = "[{}] {}".format(dt_string, message)
        return print(text)

    def start(self):
        self.print("Start proxy from '{}' to '{}'.".format(self.config.in_port, self.config.out_port))
        return super().start()


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


def run_hotdogbatch(config_file: str) -> None:
    config = Config.from_file(config_file)
    config.validate()
    publisher = MyPublisher(config.proxy)
    processor = Processor(config)
    processor.subscribe(publisher)
    processor.process_files_in_dir()
    return


def hotdogbatch() -> None:
    fire.Fire(run_hotdogbatch)
    return
