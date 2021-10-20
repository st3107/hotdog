import dataclasses
import pathlib

import numpy as np
from pkg_resources import resource_filename

import hotdog.core as core

DATA_DIR = pathlib.Path(resource_filename("hotdog", "data"))


def test_process_a_files_mode_0(tmp_path):
    config = core.Config(processor=core.ProcessorConfig(
        mode=0,
        T0=273.15,
        V0=253.82,
        prev_csv="",
        a_coeffs=[1., 6.55e-6, 1.82e-9],
        b_coeffs=[1., 6.55e-6, 1.82e-9],
        c_coeffs=[1., 6.54e-6, 2.60e-9],
        tc_path="C:\\TOPAS6\\tc.exe",
        inp_path=str(DATA_DIR.joinpath("Al2O3.inp")),
        working_dir=str(tmp_path),
        xy_file_fmt=r"{num1}Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_{x}_mm_Grid_Y_{y}_mm_a428d7_{"
                    r"num2}_dark_corrected_img.xy",
        data_keys=["x", "y"],
        metadata={},
        n_scan=1
    ))
    processor = core.Processor(config)
    processor.process_a_file(
        str(
            DATA_DIR.joinpath(
                "001Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_55,62000_mm_Grid_Y_64,00000_mm_a428d7_"
                "0001_dark_corrected_img.xy"
            )
        )
    )
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1
    print(csv_files[0].read_text())


def test_process_many_files_mode_1_no_prev_csv(tmp_path):
    config = core.Config(processor=core.ProcessorConfig(
        mode=1,
        T0=273.15,
        V0=253.82,
        prev_csv="",
        a_coeffs=[1., 6.55e-6, 1.82e-9],
        b_coeffs=[1., 6.55e-6, 1.82e-9],
        c_coeffs=[1., 6.54e-6, 2.60e-9],
        tc_path="C:\\TOPAS6\\tc.exe",
        inp_path=str(DATA_DIR.joinpath("Al2O3.inp")),
        working_dir=str(tmp_path),
        xy_file_fmt=r"{num1}Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_{x}_mm_Grid_Y_{y}_mm_a428d7_{"
                    r"num2}_dark_corrected_img.xy",
        data_keys=["x", "y"],
        metadata={},
        n_scan=2
    ))
    processor = core.Processor(config)
    processor.process_many_files(list(map(str, DATA_DIR.glob("*.xy"))))
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1
    print(csv_files[0].read_text())


def test_process_many_files_mode_1_with_prev_csv(tmp_path):
    config = core.Config(processor=core.ProcessorConfig(
        mode=1,
        T0=273.15,
        V0=253.82,
        prev_csv=str(DATA_DIR.joinpath("calib_run.csv")),
        a_coeffs=[1., 6.55e-6, 1.82e-9],
        b_coeffs=[1., 6.55e-6, 1.82e-9],
        c_coeffs=[1., 6.54e-6, 2.60e-9],
        tc_path="C:\\TOPAS6\\tc.exe",
        inp_path=str(DATA_DIR.joinpath("Al2O3.inp")),
        working_dir=str(tmp_path),
        xy_file_fmt=r"{num1}Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_{x}_mm_Grid_Y_{y}_mm_a428d7_{"
                    r"num2}_dark_corrected_img.xy",
        data_keys=["x", "y"],
        metadata={},
        n_scan=2
    ))
    processor = core.Processor(config)
    processor.process_many_files(list(map(str, DATA_DIR.glob("*.xy"))))
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1
    print(csv_files[0].read_text())


def test_process_many_files_mode_2_no_prev_csv(tmp_path):
    config = core.Config(processor=core.ProcessorConfig(
        mode=2,
        T0=273.15,
        V0=253.82,
        RT=293.15,
        prev_csv="",
        a_coeffs=[1., 6.55e-6, 1.82e-9],
        b_coeffs=[1., 6.55e-6, 1.82e-9],
        c_coeffs=[1., 6.54e-6, 2.60e-9],
        tc_path="C:\\TOPAS6\\tc.exe",
        inp_path=str(DATA_DIR.joinpath("Al2O3.inp")),
        working_dir=str(tmp_path),
        xy_file_fmt=r"{num1}Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_{x}_mm_Grid_Y_{y}_mm_a428d7_{"
                    r"num2}_dark_corrected_img.xy",
        data_keys=["x", "y"],
        metadata={},
        n_scan=2
    ))
    processor = core.Processor(config)
    processor.process_many_files(list(map(str, DATA_DIR.glob("*.xy"))))
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1
    print(csv_files[0].read_text())


def test_process_many_files_mode_2_with_prev_csv(tmp_path):
    config = core.Config(processor=core.ProcessorConfig(
        mode=2,
        T0=273.15,
        V0=253.82,
        prev_csv=str(DATA_DIR.joinpath("rt_run.csv")),
        a_coeffs=[1., 6.55e-6, 1.82e-9],
        b_coeffs=[1., 6.55e-6, 1.82e-9],
        c_coeffs=[1., 6.54e-6, 2.60e-9],
        tc_path="C:\\TOPAS6\\tc.exe",
        inp_path=str(DATA_DIR.joinpath("Al2O3.inp")),
        working_dir=str(tmp_path),
        xy_file_fmt=r"{num1}Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_{x}_mm_Grid_Y_{y}_mm_a428d7_{"
                    r"num2}_dark_corrected_img.xy",
        data_keys=["x", "y"],
        metadata={},
        n_scan=2
    ))
    processor = core.Processor(config)
    processor.process_many_files(list(map(str, DATA_DIR.glob("*.xy"))))
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1
    print(csv_files[0].read_text())


def test_create_a_server_from_file():
    config_file = str(DATA_DIR.joinpath("example_config.yaml"))
    core.Server.from_file(config_file)


def test_run_calib_2(tmp_path):
    config = core.Config(processor=core.ProcessorConfig(
        mode=2,
        T0=273.15,
        V0=254.713517245,
        prev_csv=None,
        a_coeffs=[1., 6.55e-6, 1.82e-9],
        b_coeffs=[1., 6.55e-6, 1.82e-9],
        c_coeffs=[1., 6.54e-6, 2.60e-9],
        n_coeffs=3,
        tc_path="C:\\TOPAS6\\tc.exe",
        inp_path=str(DATA_DIR.joinpath("Al2O3.inp")),
        working_dir=str(tmp_path),
        xy_file_fmt=r"{num1}Al2O3_009_double_grid_Ar_100p_20190822-210853_Grid_X_{x}_mm_Grid_Y_{y}_mm_a428d7_{"
                    r"num2}_dark_corrected_img.xy",
        data_keys=["x", "y"],
        metadata={},
        n_scan=2
    ))
    processor = core.Processor(config)
    calib_res = processor.run_calib_2(
        core.FitResult(0.0, 256.067, np.array([0.]), np.array([0.]), np.array([0.]), np.array([0.])),
        {}
    )
    print(calib_res.T)
