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

find_conda_env(){
    conda env list | grep "${@}" >/dev/null 2>/dev/null
}

if find_conda_env "^$env\s";
then
    echo "Find the '$env' environment."
else
    echo "Start creating conda '$env' environment with python '$pyversion'."
    conda create -n "$env" python="$pyversion" --yes
fi

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
