import pytest
import pathlib

from pkg_resources import resource_filename

import hotdog.core as core

DATA_DIR = pathlib.Path(resource_filename("hotdog", "data"))
CONFIG_FILE = DATA_DIR.joinpath("example_config.yaml")


# @pytest.mark.skip
def test_run_hotdogbatch():
    core.run_hotdogbatch(str(CONFIG_FILE))
