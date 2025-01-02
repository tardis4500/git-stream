$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

python -m pip install --upgrade pip
pip install --upgrade --upgrade-strategy eager setuptools wheel
pip install --upgrade --upgrade-strategy eager flit
pip freeze | ForEach-Object{$_.split('==')[0]} | ForEach-Object{pip install --upgrade $_}
flit install --only-deps --deps all
