import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, TextIO, Union

from pydantic import (
    BaseModel,
    BaseSettings,
    DirectoryPath,
    Field,
    PrivateAttr,
    root_validator,
    validator,
)
from ruamel.yaml import YAML

from dolphin import __version__ as _dolphin_version
from dolphin._log import get_log
from dolphin.utils import get_dates

from ._enums import InterferogramNetworkType, OutputFormat, UnwrapMethod, WorkflowName

PathOrStr = Union[Path, str]

__all__ = [
    "Workflow",
]


def _move_file_in_dir(path: PathOrStr, values: dict) -> Path:
    """Make sure the `path` is within `values['directory']`.

    Used for validation in different workflow steps' outputs.
    """
    p = Path(path)
    d = Path(values.get("directory", "."))
    if not p.parent == d:
        return d / p.name
    else:
        return p


class PsOptions(BaseModel):
    """Options for the PS pixel selection portion of the workflow."""

    directory: Path = Path("PS")
    output_file: Path = Path("ps_pixels.tif")
    amp_dispersion_file: Path = Path("amp_dispersion.tif")
    amp_mean_file: Path = Path("amp_mean.tif")

    amp_dispersion_threshold: float = Field(
        0.42,
        description="Amplitude dispersion threshold to consider a pixel a PS.",
        gt=0.0,
    )

    # validators: Check directory exists, and that outputs are within directory
    _move_in_dir = validator(
        "output_file",
        "amp_dispersion_file",
        "amp_mean_file",
        always=True,
        allow_reuse=True,
    )(_move_file_in_dir)


class HalfWindow(BaseModel):
    """Class to hold half-window size for multi-looking during phase linking."""

    x: int = Field(11, description="Half window size (in pixels) for x direction", gt=0)
    y: int = Field(5, description="Half window size (in pixels) for y direction", gt=0)

    def to_looks(self):
        """Convert (x, y) half-window size to (row, column) looks."""
        return 2 * self.y + 1, 2 * self.x + 1

    @classmethod
    def from_looks(cls, row_looks: int, col_looks: int):
        """Create a half-window from looks."""
        return cls(x=col_looks // 2, y=row_looks // 2)


class PhaseLinkingOptions(BaseModel):
    """Configurable options for wrapped phase estimation."""

    directory: Path = Path("linked_phase")
    ministack_size: int = Field(
        15, description="Size of the ministack for sequential estimator.", gt=1
    )
    half_window = HalfWindow()
    compressed_slc_file: Path = Path("compressed_slc.tif")
    temp_coh_file: Path = Path("temp_coh.tif")

    # validators
    _move_in_dir = validator(
        "compressed_slc_file", "temp_coh_file", allow_reuse=True, always=True
    )(_move_file_in_dir)

    @staticmethod
    def _format_date_pair(start: date, end: date, fmt="%Y%m%d") -> str:
        return f"{start.strftime(fmt)}_{end.strftime(fmt)}"


class InterferogramNetwork(BaseModel):
    """Options to determine the type of network for interferogram formation."""

    reference_idx: Optional[int] = Field(
        None,
        description=(
            "For single-reference network: Index of the reference image in the network"
        ),
    )
    max_bandwidth: Optional[int] = Field(
        None,
        description="Max `n` to form the nearest-`n` interferograms by index.",
        gt=1,
    )
    max_temporal_baseline: Optional[int] = Field(
        None,
        description="Maximum temporal baseline of interferograms",
        gt=0,
    )
    network_type = InterferogramNetworkType.SINGLE_REFERENCE

    # validation
    @root_validator
    def _check_network_type(cls, values):
        ref_idx = values.get("reference_idx")
        max_bw = values.get("max_bandwidth")
        max_tb = values.get("max_temporal_baseline")
        # Check if more than one has been set:
        if sum([ref_idx is not None, max_bw is not None, max_tb is not None]) > 1:
            raise ValueError(
                "Only one of `reference_idx`, `max_bandwidth`, or"
                " `max_temporal_baseline` can be set."
            )
        if max_tb is not None:
            values["network_type"] = InterferogramNetworkType.TEMPORAL_BASELINE
            return values

        if max_bw is not None:
            values["network_type"] = InterferogramNetworkType.BANDWIDTH
            return values

        # If nothing else specified, set to a single reference network
        values["network_type"] = InterferogramNetworkType.SINGLE_REFERENCE
        # and make sure the reference index is set
        if ref_idx is None:
            values["reference_idx"] = 0
        return values


class UnwrapOptions(BaseModel):
    """Options for unwrapping after wrapped phase estimation."""

    run_unwrap: bool = False
    directory: Path = Path("unwrap")
    unwrap_method: UnwrapMethod = UnwrapMethod.SNAPHU
    tiles: Sequence[int] = [1, 1]
    init_method: str = "mcf"

    # validators


class WorkerSettings(BaseSettings):
    """Settings configurable based on environment variables."""

    gpu_enabled: bool = Field(
        True,
        description="Whether to use GPU for processing (if available)",
    )
    gpu_id: int = Field(
        0,
        description="Index of the GPU to use for processing (if GPU)",
    )
    # n_workers: int = PositiveInt(16)
    n_workers: int = Field(
        16, ge=1, description="Number of cpu cores to use for processing (if CPU)"
    )
    max_ram_gb: float = Field(
        1.0,
        description="Maximum RAM (in GB) to use for processing",
        gt=0.1,
    )

    class Config:
        """Pydantic class configuration for BaseSettings."""

        # https://docs.pydantic.dev/usage/settings/#parsing-environment-variable-values
        env_prefix = "dolphin_"  # e.g. DOLPHIN_N_WORKERS=4 for n_workers
        fields = {
            "gpu_enabled": {"env": ["dolphin_gpu_enabled", "gpu"]},
        }


class Inputs(BaseModel):
    """Options specifying input datasets for workflow."""

    cslc_directory: DirectoryPath = Field(None, description="Path to CSLC files")
    cslc_file_ext: Optional[str] = Field(
        ".nc",
        description="Extension of CSLC files (if providing `cslc_directory`)",
    )
    cslc_file_list: List[PathOrStr] = Field(
        default_factory=list, description="List of CSLC files"
    )
    cslc_date_fmt: str = Field(
        "%Y%m%d",
        description="Format of dates contained in CSLC filenames",
    )

    mask_files: List[str] = Field(
        default_factory=list,
        description=(
            "List of mask files to use, where convention is"
            " 0 for no data/invalid, and 1 for data."
        ),
    )

    # validators
    @validator("mask_files", "cslc_file_list", pre=True)
    def _check_mask_files(cls, v):
        if isinstance(v, str):
            return [v]
        elif v is None:
            return []
        return v

    @root_validator
    def _check_slc_files_exist(cls, values):
        file_list = values.get("cslc_file_list")
        directory = values.get("cslc_directory")
        if not file_list:
            if not directory:
                raise ValueError("Must specify either cslc_file_list or cslc_directory")

            ext = values.get("cslc_file_ext")
            file_list = sorted(directory.glob(f"*{ext}"))
            # Filter out files that don't have dates in the filename
            date_fmt = values.get("cslc_date_fmt")
            file_list = [str(f) for f in file_list if get_dates(f, fmt=date_fmt)]
            values["cslc_file_list"] = file_list
            # Save the directory, if used, as an absolute path
            values["cslc_directory"] = directory.absolute()
        else:
            # If the file_list is directly provided, null out the directory/extension
            values["cslc_directory"] = None
            values["cslc_file_ext"] = None
        return values


class Outputs(BaseModel):
    """Options for the output format/compressions."""

    output_format: OutputFormat = OutputFormat.NETCDF
    scratch_directory: Path = Path("scratch")
    output_directory: Path = Path("output")
    output_resolution: List[float] = Field(
        [20, 20],
        description="Output (x, y) resolution (in units of input data)",
    )
    strides: List[int] = Field(
        [1, 1],
        description=(
            "Alternative to specifying output resolution: Specify the (x, y) strides"
            " (decimation factor) to perform while processing input. For example,"
            " strides of [4, 2] would turn an input resolution of [5, 10] into an"
            " output resolution of [20, 20]."
        ),
    )

    hdf5_creation_options: Dict = Field(
        dict(
            chunks=True,
            compression="gzip",
            compression_opts=4,
            shuffle=True,
        ),
        description="Options for `create_dataset` with h5py.",
    )
    gtiff_creation_options: List[str] = Field(
        ["TILED=YES", "COMPRESS=DEFLATE", "ZLEVEL=5"],
        description="GDAL creation options for GeoTIFF files",
    )

    # validators
    @validator("output_directory", "scratch_directory", always=True)
    def _dir_is_absolute(cls, v):
        return v.absolute()


class Workflow(BaseModel):
    """Configuration for the workflow.

    Required fields are in `Inputs`.
    Must specify either `cslc_file_list`, or `cslc_directory` and
    a `cslc_file_ext`.
    """

    workflow_name: str = WorkflowName.STACK

    inputs: Inputs
    outputs: Outputs = Field(default_factory=Outputs)

    # Options for each step in the workflow
    ps_options: PsOptions = Field(default_factory=PsOptions)
    phase_linking: PhaseLinkingOptions = Field(default_factory=PhaseLinkingOptions)
    interferogram_network: InterferogramNetwork = Field(
        default_factory=InterferogramNetwork
    )
    unwrap_options: UnwrapOptions = Field(default_factory=UnwrapOptions)

    # General workflow metadata
    worker_settings: WorkerSettings = Field(default_factory=WorkerSettings)
    creation_time_utc: datetime = Field(default_factory=datetime.utcnow)
    dolphin_version: str = _dolphin_version

    # internal helpers
    # Stores the list of directories to be created by the workflow
    _directory_list: List[Path] = PrivateAttr(default_factory=list)

    # validators
    @root_validator
    def _move_dirs_inside_scratch(cls, values):
        """Ensure outputs from workflow steps are within scratch directory."""
        scratch_dir = values["outputs"].scratch_directory
        # Save all directories as absolute paths
        scratch_dir = scratch_dir.absolute()

        # For each workflow step that has an output folder, move it inside
        # the scratch directory (if it's not already inside).
        # They may already be inside if we're loading from a json/yaml file.
        ps_opts = values["ps_options"]
        if not ps_opts.directory.parent == scratch_dir:
            ps_opts.directory = scratch_dir / ps_opts.directory
        ps_opts.directory = ps_opts.directory.absolute()

        if not ps_opts.amp_dispersion_file.parent.parent == scratch_dir:
            ps_opts.amp_dispersion_file = scratch_dir / ps_opts.amp_dispersion_file
        if not ps_opts.amp_mean_file.parent.parent == scratch_dir:
            ps_opts.amp_mean_file = scratch_dir / ps_opts.amp_mean_file
        if not ps_opts.output_file.parent.parent == scratch_dir:
            ps_opts.output_file = scratch_dir / ps_opts.output_file

        pl_opts = values["phase_linking"]
        if not pl_opts.directory.parent == scratch_dir:
            pl_opts.directory = scratch_dir / pl_opts.directory
        pl_opts.directory = pl_opts.directory.absolute()

        if not pl_opts.compressed_slc_file.parent.parent == scratch_dir:
            pl_opts.compressed_slc_file = scratch_dir / pl_opts.compressed_slc_file
        if not pl_opts.temp_coh_file.parent.parent == scratch_dir:
            pl_opts.temp_coh_file = scratch_dir / pl_opts.temp_coh_file

        unw_opts = values["unwrap_options"]
        if not unw_opts.directory.parent == scratch_dir:
            unw_opts.directory = scratch_dir / unw_opts.directory
        unw_opts.directory = unw_opts.directory.absolute()

        return values

    # Extra model exporting options beyond .dict() or .json()
    def to_yaml(self, output_path: Union[PathOrStr, TextIO]):
        """Save workflow configuration as a yaml file.

        Used to record the default-filled version of a supplied yaml.

        Parameters
        ----------
        output_path : Pathlike
            Path to the yaml file to save.
        """
        data = json.loads(self.json())
        y = YAML()
        if hasattr(output_path, "write"):
            y.dump(data, output_path)
        else:
            with open(output_path, "w") as f:
                y.dump(data, f)

    @classmethod
    def from_yaml(cls, yaml_path: PathOrStr):
        """Load a workflow configuration from a yaml file.

        Parameters
        ----------
        yaml_path : Pathlike
            Path to the yaml file to load.

        Returns
        -------
        Config
            Workflow configuration
        """
        y = YAML(typ="safe")
        with open(yaml_path, "r") as f:
            data = y.load(f)
        return cls(**data)

    def __init__(self, **data):
        """After validation, initialize and store the directory list."""
        super().__init__(**data)
        # Track the directories that need to be created at start of workflow
        self._directory_list = [
            self.outputs.scratch_directory,
            self.outputs.output_directory,
            self.ps_options.directory,
            self.phase_linking.directory,
            self.unwrap_options.directory,
        ]

    def create_dir_tree(self, debug=False):
        """Create the directory tree for the workflow."""
        log = get_log(debug=debug)
        for d in self._directory_list:
            log.debug(f"Creating directory: {d}")
            d.mkdir(parents=True, exist_ok=True)
