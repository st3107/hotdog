============
Installation
============

Installation from source
------------------------

Require bash shell with `conda` installed.

At the command line::

    git clone https://github.com/st3107/hotdog.git;
    conda env create -n hotdog -f ./hotdog/test-env.yaml

Activate the environment::

    conda activate hotdog

Install the package::

    pip install -e ./hotdog



Update the package
------------------

If you have already installed the package but didn't get the latest version, please open a terminal and run::

    cd hotdog
    git pull origin main
    pip install -e ./hotdog

Please change the `hotdog` here to the path to the hotdog folder on your machine.