import pathlib
import shutil
import typing as T
from dataclasses import fields

import hotdog.core as core
import pandas as pd
import pytest
import xarray as xr
from databroker import Broker
from pkg_resources import resource_filename

_DATA_DIR = pathlib.Path(resource_filename("hotdog", "data"))


def prepare_work_space(root_dir: pathlib.Path) -> core.Config:
    # copy the inp file
    inp_name = "Al2O3.inp"
    src_inp = _DATA_DIR.joinpath(inp_name)
    tgt_inp = root_dir.joinpath(inp_name)
    shutil.copy(str(src_inp), str(tgt_inp))
    # assign csv path
    tgt_csv = root_dir.joinpath("hotdog_data.csv")
    # create a directory for inputs
    watch_path = root_dir.joinpath("inputs")
    watch_path.mkdir(exist_ok=True)
    # ceate fake topas exe
    tc_path = root_dir.joinpath("tc.exe")
    tc_path.touch()
    # write config
    config_file = _DATA_DIR.joinpath("example_config.yaml")
    config = core.Config.from_file(str(config_file))
    config.processor.inp_path = str(tgt_inp)
    config.processor.working_dir = str(root_dir)
    config.observer.watch_path = str(watch_path)
    config.processor.prev_csv = str(tgt_csv)
    config.processor.tc_path = str(tc_path)
    config.processor.is_test = True
    save_config_file(config)
    return config


def save_config_file(config: core.Config) -> str:
    yaml_file = pathlib.Path(config.processor.working_dir).joinpath("config.yaml")
    config.to_yaml(str(yaml_file))
    return str(yaml_file)


@pytest.fixture
def ready_config(tmp_path: pathlib.Path) -> core.Config:
    return prepare_work_space(tmp_path)


@pytest.fixture
def ready_config_file(ready_config) -> str:
    return save_config_file(ready_config)


def copy_xy_files(config: core.Config):
    src_xys = _DATA_DIR.glob(r"[!.]*.xy")
    for src_xy in src_xys:
        tgt_xy = pathlib.Path(config.observer.watch_path).joinpath(src_xy.name)
        shutil.copy(str(src_xy), str(tgt_xy))
    return


def get_expected_data_keys(config: core.Config) -> T.Set[str]:
    extracted_keys = set(config.processor.data_keys)
    calib_result_keys = {f.name for f in fields(core.CalibResult)}
    fit_result_keys = {f.name for f in fields(core.FitResult)}
    expect_data_keys = extracted_keys.union(calib_result_keys).difference(fit_result_keys)
    return expect_data_keys


def get_expected_columns(config: core.Config) -> T.Set[str]:
    other_keys = {"time", "filename"}
    array_keys = {"Idiff", "Icalc", "I", "tth"}
    expected_data_keys = get_expected_data_keys(config)
    return expected_data_keys.union(other_keys).difference(array_keys)


def get_output_dataframe(config: core.Config) -> pd.DataFrame:
    prev_csv = pathlib.Path(config.processor.prev_csv)
    assert prev_csv.is_file()
    df: pd.DataFrame = pd.read_csv(str(prev_csv), index_col=0)
    return df


def check_dataframe_correctness(config: core.Config) -> None:
    df = get_output_dataframe(config)
    assert not df.empty
    assert df.shape[0] > 0
    columns = set(df.columns)
    expect_columns = get_expected_columns(config)
    for col in expect_columns:
        assert col in columns
    return


def check_database_correctness(config: core.Config, db: Broker) -> None:
    run = db[-1]
    data: xr.Dataset = run.primary.read()
    output_data_keys = set(data.variables.keys())
    expect_data_keys = get_expected_data_keys(config)
    for key in expect_data_keys:
        assert key in output_data_keys
    return


def test_Processor(ready_config: core.Config):
    config = ready_config
    copy_xy_files(config)
    db = Broker.named("temp").v2
    processor = core.Processor(config)
    processor.subscribe(db.v1.insert)
    processor.process_files_in_dir()
    check_database_correctness(config, db)
    check_dataframe_correctness(config)


def test_run_hotdogbatch(ready_config_file: str):
    config_file = ready_config_file
    config = core.Config.from_file(config_file)
    copy_xy_files(config)
    core.run_hotdogbatch(config_file)
    check_dataframe_correctness(config)


def test_start_hotdog_server(ready_config_file: str):
    core.Server.from_file(ready_config_file)


def test_start_vis_server(ready_config_file: str):
    core.VisServer.from_file(ready_config_file)
