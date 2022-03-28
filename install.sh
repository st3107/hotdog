#!/bin/bash

env="${1:-hotdog}"
mode="${2:-user}"

echo "Start creating conda environment '$env'."
conda create -n "$env" --yes

echo "Start installing in the '$mode' mode."
conda install -n "$env" -c conda-forge --file requirements.txt --yes
if [ "$mode" == "developer" ]
then
    conda install -n "$env" -c conda-forge --file requirements-dev.txt --freeze-installed --yes
fi

echo "Finish installation."
echo "Please activate the conda environment before using the package."
echo ""
echo "    conda activate $env"