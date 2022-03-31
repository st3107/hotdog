#!/bin/bash

set -e
env="${env:-hotdog}"
mode="${mode:-user}"
pyversion="${pyversion:-3.9}"

if [ "$mode" == "user" ]
then
    echo "Download latest sotware."
    git stash
    git checkout main
    git pull origin main
fi

echo "Start creating conda '$env' environment with python '$pyversion'."
conda create -n "$env" python="$pyversion" --yes

echo "Start installing in the '$env' environment with '$mode' mode."
conda install -n "$env" -c conda-forge --file requirements.txt --yes
if [ "$mode" == "developer" ]
then
    conda install -n "$env" -c conda-forge --file requirements-dev.txt --freeze-installed --yes
    conda run -n "$env" --live-stream python -m pip install -e . --no-deps
else
    conda run -n "$env" --live-stream python -m pip install . --no-deps
fi

echo "Finish installation."
echo "Please activate the conda environment before using the package."
echo ""
echo "    conda activate $env"
