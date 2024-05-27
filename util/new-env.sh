#!/usr/bin/env bash
set -e

source $VIRTUALENVWRAPPER_SCRIPT
mkvirtualenv git-stream
python -m pip install --upgrade pip
pip install --upgrade --upgrade-strategy eager setuptools wheel
pip install --upgrade --upgrade-strategy eager flit
flit install -s --deps all

# cSpell:ignore mkvirtualenv
