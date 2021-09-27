import pathlib
from pkg_resources import resource_filename

import hotdog.core as core

DATA_DIR = pathlib.Path(resource_filename("hotdog", "data"))


def test_Processor(tmp_path):
    config = core.ProcessorConfig(
        V0_path=str(DATA_DIR.joinpath("result_at_T0.csv")),
        V0=1.,
        T0=298.15,
        c1=0.,
        c2=0.,
        tc_path="",
        inp_path="",
        wd_path=str(tmp_path),
        xy_file_fmt="",
        data_keys=tuple(),
        metadata=None,
        n_scan=1,
        n_thread=1
    )
    processor = core.Processor(config)
    processor.process_many_files(map(str, DATA_DIR.glob("*.xy")))
