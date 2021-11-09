=====
Usage
=====


What does it do?
----------------

The server will process the data files in a stream. The data files are the two column text files of XRD pattern.
The output files are csv files. The columns are the volume of the cell and the temperature at the point of each
measurement. The time and other information like horizontal and vertical positions will also be recored if
specified.

The algorithm is to fit the data using TOPAS and get the volume `Vobs` of the cell. Then, correct the volume
using `Vreal = alpha * Vobs`. Then, plug it into the equation `Vreal = V0 * polynomial(T - T0)` to calculate the
temperature `T`.

Here, the `V0` can be directly specified or it can be calculated according to the `Vreal` from a room temperature
scan. The `alpha` is calculated from the room temperature scan and a data point at the position where the
calibration is done.

How to use it?
--------------

Prepare a configuration file in current working directory. One example file can be found `here <https://github
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


How to use it after the collection?
-----------------------------------

If we already have a folder with all the data files inside, we cann run the `hotdogbatch` to process them. It requires
the same configuration file as the hotdog.

Open three bash terminals and conda activate the environment in each of them (here, it is named "hotdog".)::

    conda activate hotdog


If you would like the streaming visualization during the data processing, start the proxy::

    hotdogproxy example_config.yaml

Then, start the visualization server::

    hotdogvis example_config.yaml

Finally, run the command::

    hotdogbatch example_config.yaml


