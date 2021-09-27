import collections
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

    VT0_path: typing.Union[None, str] = None
    c1: float = 0.
    c2: float = 0.
    V_col: str = "Vol"
    T_col: str = "T"
    tc_path: str = ""
    inp_path: str = ""
    xy_file: str = "xy_file"
    res_file: str = "res_file"
    fit_file: str = "fit_file"
    wd_path: str = ""
    xy_file_fmt: str = ""
    data_keys: typing.Tuple = tuple()
    metadata: dict = None
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
        vt0_path = self.config.VT0_path
        self.vt0_df: pd.DataFrame = pd.read_csv(vt0_path) if vt0_path is not None else pd.DataFrame()
        self.inp_template = pathlib.Path(self.config.inp_path).read_text()
        self.working_dir = pathlib.Path(self.config.wd_path)
        self.desc_uid = ""
        self.count = 0

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
        cr = self.run_calib(fr) if not self.vt0_df.empty else {}
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

    def run_calib(self, fitresult: FitResult) -> CalibResult:
        v = fitresult.Vol
        c1 = self.config.c1
        c2 = self.config.c2
        v0 = self.vt0_df[self.config.V_col][self.count - 1]
        t0 = self.vt0_df[self.config.T_col][self.count - 1]
        _, T = np.roots([c2, c1, v0 - c2 * t0 ** 2 - c1 * t0 - v])
        return CalibResult(T=T)

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
