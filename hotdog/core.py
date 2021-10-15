import collections
import dataclasses
import pathlib
import subprocess
import typing
from dataclasses import dataclass
import dataclasses as dcs
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import numpy as np
import tqdm
from bluesky.callbacks.stream import LiveDispatcher
import bluesky.utils as bus
import intake.source.utils as isu


@dataclass
class Config:

    observer: ObserverConfig
    processor: ProcessorConfig


@dataclass
class ObserverConfig:
    # TODO: fill in
    pass


@dataclass
class ProcessorConfig:

    mode: int = 0
    T0: float = 0.
    V0: float = 0.
    prev_csv: str = None
    coeffs: typing.Tuple = None
    vol_correction: pd.DataFrame = None
    tc_path: str = None
    inp_path: str = None
    xy_file: str = "xy_file"
    res_file: str = "res_file"
    fit_file: str = "fit_file"
    wd_path: str = None
    xy_file_fmt: str = None
    data_keys: typing.Tuple = None
    metadata: typing.Dict[str, float] = None
    n_scan: int = 1
    n_thread: int = 1


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


class Observer:
    """Monitor the directory and let Processor process the newly created files."""
    #TODO: fill in
    pass


class Processor(LiveDispatcher):
    """Process the data file and publish the results in an event stream."""

    def __init__(self, config: ProcessorConfig):
        super(Processor, self).__init__()
        self.config = config
        self.prev_df = None
        if self.config.prev_csv is not None:
            self.prev_df = pd.read_csv(self.config.prev_csv)
        else:
            if self.config.mode == 1:
                self.print(
                    "WARNING: Missing the data from the previous run. "
                    "The alpha will not be calculated."
                )
            elif self.config.mode == 2:
                self.print(
                    "WARNING: Missing the data from the previous run. "
                    "The volume correction will be skipped. "
                    "The T0 and V0 in configuration file will be used."
                )
        self.inp_template = pathlib.Path(self.config.inp_path).read_text()
        self.working_dir = pathlib.Path(self.config.wd_path)
        self.desc_uid = ""
        self.count = 0
        self.fields = tuple((f.name for f in dataclasses.fields(CalibResult)))

    def process_a_file(self, filename: str) -> None:
        """Process the XRD data file and output the documents of the results.

        The fitted data file and result csv file will be generated in the process.

        Parameters
        ----------
        filename : str
            The path to the XRD data file.
        """
        # count
        self.count += 1
        # process file
        raw_data, raw_meta = self.parse_filename(filename)
        fr = self.run_topas(filename)
        cr = self.run_calib(fr, raw_data)
        data = dict(**raw_data, **dcs.asdict(fr), **dcs.asdict(cr))
        # emit start if this is the first file
        if self.count == 1:
            self.emit_start(raw_meta)
            self.emit_descriptor()
        # emit event data
        self.process_event({"data": data, "descriptor": self.desc_uid})
        # emit stop if this is the last file
        if self.count == self.config.n_scan:
            self.emit_stop()
            self.count = 0
        return

    def process_many_files(self, filenames: typing.Iterable[str]) -> None:
        n_thread = self.config.n_thread
        exe = ThreadPoolExecutor(max_workers=n_thread)
        exe.map(self.process_a_file, filenames)
        return

    def run_topas(self, filename: str) -> FitResult:
        inp = self.inp_template
        wd = self.working_dir
        tc_path = self.config.tc_path
        # get all file paths
        xy_file = pathlib.PurePath(filename)
        out_fp = wd.joinpath(xy_file.stem)
        inp_file = out_fp.with_suffix(".inp")
        res_file = out_fp.with_suffix(".res")
        fit_file = out_fp.with_suffix(".fit")
        # write out the inp file
        inp_text = inp.format(
            **dict(
                zip(
                    [self.config.xy_file, self.config.res_file, self.config.fit_file],
                    [str(xy_file), str(res_file), str(fit_file)]
                )
            )
        )
        if inp_file.is_file():
            raise ProcessorError("{} already exits.".format(str(inp_file)))
        inp_file.touch()
        inp_file.write_text(inp_text)
        # run topas on this file
        cmd = [tc_path, str(inp_file)]
        cp = subprocess.run(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # if run fails, raise error
        if cp.returncode != 0:
            raise ProcessorError(cp.stderr)
        # read result
        if not res_file.is_file():
            raise ProcessorError("{} doesn't exist.".format(str(res_file)))
        res = np.loadtxt(str(res_file), delimiter=",", dtype=float)
        if not (res.ndim == 1 and res.shape[0] == 2):
            raise ProcessorError("The {} is not one-row and two-column file.".format(str(res_file)))
        # read fit
        if not fit_file.is_file():
            raise ProcessorError("{} doesn't exist.".format(str(fit_file)))
        fit = np.loadtxt(str(fit_file), delimiter=",", dtype=float).transpose()
        if not (fit.ndim == 2 and fit.shape[0] == 4):
            raise ProcessorError("The {} is not four-column file.".format(str(fit_file)))
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
        return CalibResult(1., fitresult.Vol, self.config.T0)

    def run_calib_1(self, fitresult: FitResult) -> CalibResult:
        if self.prev_df is None:
            realVol = fitresult.Vol
            alpha = 1
        else:
            realVol = self.prev_df["realVol"][0]
            alpha = realVol / fitresult.Vol
        T = self.prev_df["T"][0]
        return CalibResult(alpha, realVol, T)

    def run_calib_2(self, fitresult: FitResult, raw_data: dict) -> CalibResult:
        if self.prev_df is None:
            cr0 = 1., self.config.T0, self.config.V0
        else:
            cr0 = self._get_prev_result(raw_data)
        alpha = cr0.alpha
        realVol = alpha * fitresult.Vol
        T = np.max(np.roots((cr0.realVol - realVol,) + self.config.coeffs)) + cr0.T
        return CalibResult(alpha, realVol, T)

    def _get_prev_result(self, raw_data: dict) -> CalibResult:
        keys = list(raw_data.keys())
        sel_prev_df = self.prev_df[keys]
        raw_df = pd.DataFrame(raw_data)
        idx = (sel_prev_df - raw_df).pow(2).sum(axis=0).argmin()
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

    def parse_filename(self, filename: str) -> typing.Tuple[dict, dict]:
        xy_file_fmt = self.config.xy_file_fmt
        data_keys = self.config.data_keys
        # parse file name
        xy_file = pathlib.PurePath(filename)
        dct = isu.reverse_format(xy_file_fmt, xy_file.name)
        # split it to data and metadata
        data, meta = dict(), dict()
        for key, val in dct.items():
            if key in data_keys:
                data[key] = val
            else:
                meta[key] = val
        return data, meta

    def emit_descriptor(self) -> str:
        uid = bus.new_uid()
        self.descriptor({"uid": uid, "data_keys": {}})
        self.desc_uid = uid
        return uid

    def emit_stop(self) -> str:
        uid = bus.new_uid()
        self.stop({"uid": uid})
        return uid
