======
hotdog
======

.. image:: https://github.com/st3107/hotdog/actions/workflows/test.yml/badge.svg
    :target: https://github.com/st3107/hotdog/actions/workflows/test.yml
.. image:: https://github.com/st3107/hotdog/actions/workflows/docs.yml/badge.svg
    :target: https://github.com/st3107/hotdog/actions/workflows/docs.yml


A python package to calibrate the temperature distribution in the floating zone furnace.

* Free software: 3-clause BSD license
* Documentation: https://st3107.github.io/hotdog.

Installation from source
------------------------

Require `bash` shell with `conda` installed.

At the command line::

    git clone https://github.com/st3107/hotdog.git
    cd hotdog
    bash install.sh


Installation for developers
---------------------------

Please fork the [repo](https://github.com/st3107/hotdog.git) and then run::

    git clone https://github.com/<your account>/hotdog.git
    cd hotdog
    mode=developer bash install.sh

Change `<your account>` to your account name.

Update the package
------------------

If you have already installed the package but didn't get the latest version, please open a terminal and run::

    cd hotdog
    git pull origin main
    bash install.sh

Please change the `hotdog` here to the path to the hotdog folder on your machine.
