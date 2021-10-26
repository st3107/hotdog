=====
Usage
=====


Use the CLI
-----------

Prepare a configuration file. One example file can be found `here <https://github
.com/st3107/hotdog/blob/main/hotdog/data/example_config.yaml>`_.

It is a yaml file. If you are not familiar with yaml file. Please read this `website <https://yaml.org/>`_
. The meaning of the keys are described in the comments in the file.

Prepare the INP file for the TOPAS. Here is an `example <https://github
.com/st3107/hotdog/blob/main/hotdog/data/Al2O3.inp>`_. It must include three words "xy_file", "res_file",
"fit_file". They will be replaced by the file path for each run.

Open three bash terminals and conda activate the environment in each of them (here, it is named "hotdog".)::

    conda activate hotdog

In the first terminal, start the data processing server (here, we use "example_config.yaml" as the config file.)::

    hotdog example_config.yaml

In the second terminal, start the proxy server::

    hotdogproxy example_config.yaml

In the third terminal, start the data visualization server::

    hotdogvis example_config.yaml

The data processing and saving are done by the first server.The second and the third servers are for the
visualization.

If you didn't see any error messages, the servers have been successfully started.
