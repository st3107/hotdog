import pathlib
import shutil
from dataclasses import fields

import hotdog.core as core
import pandas as pd
import pytest
import xarray as xr
from databroker import Broker
from pkg_resources import resource_filename

_DATA_DIR = pathlib.Path(resource_filename("hotdog", "data"))


@pytest.fixture
def ready_config(tmp_path: pathlib.Path) -> core.Config:
    # move the files
    inp_name = "Al2O3.inp"
    src_inp = _DATA_DIR.joinpath(inp_name)
    tgt_inp = tmp_path.joinpath(inp_name)
    shutil.copy(str(src_inp), str(tgt_inp))
    src_xys = _DATA_DIR.glob(r"[!.]*.xy")
    for src_xy in src_xys:
        tgt_xy = tmp_path.joinpath(src_xy.name)
        shutil.copy(str(src_xy), str(tgt_xy))
    tgt_csv = tmp_path.joinpath("hotdog_data.csv")
    # write config
    config_file = _DATA_DIR.joinpath("example_config.yaml")
    config = core.Config.from_file(str(config_file))
    config.processor.inp_path = str(tgt_inp)
    config.processor.working_dir = str(tmp_path)
    config.observer.watch_path = str(tmp_path)
    config.processor.is_test = True
    config.processor.prev_csv = str(tgt_csv)
    return config


def test_Processor(ready_config: core.Config):
    config = ready_config
    db = Broker.named("temp").v2
    processor = core.Processor(config)
    processor.subscribe(db.v1.insert)
    processor.process_files_in_dir()
    run = db[-1]
    data: xr.Dataset = run.primary.read()
    output_data_keys = set(data.variables.keys())
    extracted_keys = set(config.processor.data_keys)
    calib_result_keys = {f.name for f in fields(core.CalibResult)}
    fit_result_keys = {f.name for f in fields(core.FitResult)}
    other_keys = {"time", "filename"}
    array_keys = {"Idiff", "Icalc", "I", "tth"}
    expect_data_keys = extracted_keys.union(calib_result_keys).difference(fit_result_keys)
    for key in expect_data_keys:
        assert key in output_data_keys
    df: pd.DataFrame = pd.read_csv(config.processor.prev_csv, index_col=0)
    assert not df.empty
    columns = set(df.columns)
    expect_columns = expect_data_keys.union(other_keys).difference(array_keys)
    for col in expect_columns:
        assert col in columns
