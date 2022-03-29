#!/bin/bash

env="${env:-hotdog}"
mode="${mode:-user}"
pyversion="${pyversion:-3.9}"

echo "Start creating conda environment '$env' with python '$pyversion'."
conda create -n "$env" python="$pyversion" --yes

echo "Start installing in the '$mode' mode."
conda install -n "$env" -c conda-forge --file requirements.txt --yes
if [ "$mode" == "developer" ]
then
    conda install -n "$env" -c conda-forge --file requirements-dev.txt --freeze-installed --yes
    conda run -n "$env" --live-stream python -m pip install -e .
else
    conda run -n "$env" --live-stream python -m pip install .
fi

echo "Finish installation."
echo "Please activate the conda environment before using the package."
echo ""
echo "    conda activate $env"
