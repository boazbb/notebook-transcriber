#!/bin/sh
# Copy the helper's code to the frozen copy that the installed app uses (release/helper).
# The Python environments (.venv, kraken_env) are shared with ../helper through links.
# Run when a new version is finished and tested; then restart the app.
set -e
cd "$(dirname "$0")"
mkdir -p helper
rsync -a --delete --exclude "__pycache__" ../helper/notebook_helper/ helper/notebook_helper/
cp ../helper/pyproject.toml ../helper/uv.lock helper/
[ -e helper/.venv ] || ln -s ../../helper/.venv helper/.venv
[ -e helper/kraken_env ] || ln -s ../../helper/kraken_env helper/kraken_env
date "+%Y-%m-%d %H:%M:%S" > helper/VERSION
echo "frozen helper updated: $(cat helper/VERSION)"
