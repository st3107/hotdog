#!/bin/bash

set -e
env="${env:-hotdog}"
mode="${mode:-user}"

echo "Download latest sotware."
git stash
git checkout main
git pull origin main

echo "Update dependencies in '$mode' mode."
conda update -n "$env" -c conda-forge --file requirements.txt --yes
if [ "$mode" == "developer" ]
then
    conda update -n "$env" -c conda-forge --file requirements-dev.txt --yes
fi

echo "Update the packge in '$env' environment."
if [ "$mode" == "developer" ]
then
    conda run -n "$env" --live-stream python -m pip install -e . --no-deps
else
    conda run -n "$env" --live-stream python -m pip install . --no-deps
fi

echo "Finish update."
echo "Please activate the conda environment before using the package."
echo ""
echo "    conda activate $env"
