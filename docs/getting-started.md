

## Usage

Dolphin has a main command line entry point to run the algorithms and tools in workflows.
The main entry point is named `dolphin`, which has two subcommands:

1. `dolphin config`: create a workflow configuration file.
2. `dolphin run` : run the workflow using this file.

Example usage:

```bash
$ dolphin config --slc-directory /path/to/slc --ext ".tif"
```
This will create a YAML file (by default `dolphin_config.yaml` in the current directory).
You can also directly use a list of SLC files as input, e.g.:
```bash
$ dolphin config --slc-files /path/to/slc1.tif /path/to/slc2.tif
```



## Install

The following will install `dolphin` into a conda environment.

1. Download source code:
```bash
git clone https://github.com/opera-adt/dolphin.git && cd dolphin
```
2. Install dependencies:
```bash
conda install -c conda-forge --file requirements.txt
```

3. Install `dolphin` via pip:
```bash
python -m pip install .
```

## Setup for Developers

To setup a development environment, you can use the following additional steps:


```bash
# run "pip install -e" to install with extra development requirements
python -m pip install -e ".[docs,test]"
```
This will install the `dolphin` package in development mode, and install the additional dependencies for documentation and testing.

After changing code, we use [`pre-commit`](https://pre-commit.com/) to automatically run linting and formatting:
```bash
# Get pre-commit hooks so that linting/formatting is done automatically
pre-commit install

After making changes, you can rerun the existing tests and any new ones you have added using:
```bash
pytest
```


### Creating Documentation


We use [MKDocs](https://www.mkdocs.org/) to generate the documentation.
The reference documentation is generated from the code docstrings using [mkdocstrings](mkdocstrings.github.io/).

When adding new documentation, you can build and serve the documentation locally using:

```
mkdocs serve
```
then open http://localhost:8000 in your browser.
Creating new files or updating existing files will automatically trigger a rebuild of the documentation while `mkdocs serve` is running.
