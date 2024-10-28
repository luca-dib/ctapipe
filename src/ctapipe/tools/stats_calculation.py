"""
Perform statistics calculation from DL1a image data
"""

import pathlib

import numpy as np
from astropy.table import vstack

from ctapipe.core import Tool
from ctapipe.core.tool import ToolConfigurationError
from ctapipe.core.traits import (
    Bool,
    CaselessStrEnum,
    CInt,
    Path,
    Set,
    Unicode,
    classes_with_traits,
)
from ctapipe.instrument import SubarrayDescription
from ctapipe.io import write_table
from ctapipe.io.tableloader import TableLoader
from ctapipe.monitoring.calculator import PixelStatisticsCalculator


class StatisticsCalculatorTool(Tool):
    """
    Perform statistics calculation for DL1a image data
    """

    name = "StatisticsCalculatorTool"
    description = "Perform statistics calculation for DL1a image data"

    examples = """
    To calculate statistics of DL1a image data files:

    > ctapipe-stats-calculation --input_url input.dl1.h5 --output_path /path/monitoring.h5 --overwrite

    """

    input_url = Path(
        help="Input CTA HDF5 files including DL1a image data",
        allow_none=True,
        exists=True,
        directory_ok=False,
        file_ok=True,
    ).tag(config=True)

    allowed_tels = Set(
        trait=CInt(),
        default_value=None,
        allow_none=True,
        help=(
            "List of allowed tel_ids, others will be ignored. "
            "If None, all telescopes in the input stream will be included."
        ),
    ).tag(config=True)

    dl1a_column_name = CaselessStrEnum(
        ["image", "peak_time", "variance"],
        default_value="image",
        allow_none=False,
        help="Column name of the DL1a image data to calculate statistics",
    ).tag(config=True)

    output_column_name = Unicode(
        default_value="statistics",
        allow_none=False,
        help="Column name of the output statistics",
    ).tag(config=True)

    output_path = Path(
        help="Output filename", default_value=pathlib.Path("monitoring.h5")
    ).tag(config=True)

    overwrite = Bool(help="Overwrite output file if it exists").tag(config=True)

    aliases = {
        ("i", "input_url"): "StatisticsCalculatorTool.input_url",
        ("o", "output_path"): "StatisticsCalculatorTool.output_path",
    }

    flags = {
        "overwrite": (
            {"StatisticsCalculatorTool": {"overwrite": True}},
            "Overwrite existing files",
        ),
    }

    classes = classes_with_traits(PixelStatisticsCalculator)

    def setup(self):
        # Check that the input and output files are not the same
        if self.input_url == self.output_path:
            raise ToolConfigurationError(
                "Input and output files are same. Fix your configuration / cli arguments."
            )

        # Load the subarray description from the input file
        subarray = SubarrayDescription.from_hdf(self.input_url)
        # Initialization of the statistics calculator
        self.stats_calculator = PixelStatisticsCalculator(
            parent=self, subarray=subarray
        )
        # Read the input data with the 'TableLoader'
        input_data = TableLoader(input_url=self.input_url)
        # Get the telescope ids from the input data or use the allowed_tels configuration
        tel_ids = subarray.tel_ids if self.allowed_tels is None else self.allowed_tels
        # Read the whole dl1 images
        self.dl1_tables = input_data.read_telescope_events_by_id(
            telescopes=tel_ids,
            dl1_images=True,
            dl1_parameters=False,
            dl1_muons=False,
            dl2=False,
            simulated=False,
            true_images=False,
            true_parameters=False,
            instrument=False,
            pointing=False,
        )

    def start(self):
        # Iterate over the telescope ids and their corresponding dl1 tables
        for tel_id, dl1_table in self.dl1_tables.items():
            # Perform the first pass of the statistics calculation
            aggregated_stats = self.stats_calculator.first_pass(
                table=dl1_table,
                tel_id=tel_id,
                col_name=self.dl1a_column_name,
            )
            # Check if 'chunk_shift' is selected
            if self.stats_calculator.chunk_shift is not None:
                # Check if there are any faulty chunks to perform a second pass over the data
                if np.any(~aggregated_stats["is_valid"].data):
                    # Perform the second pass of the statistics calculation
                    aggregated_stats_secondpass = self.stats_calculator.second_pass(
                        table=dl1_table,
                        valid_chunks=aggregated_stats["is_valid"].data,
                        tel_id=tel_id,
                        col_name=self.dl1a_column_name,
                    )
                    # Stack the statistic values from the first and second pass
                    aggregated_stats = vstack(
                        [aggregated_stats, aggregated_stats_secondpass]
                    )
                    # Sort the stacked aggregated statistic values by starting time
                    aggregated_stats.sort(["time_start"])
                else:
                    self.log.info(
                        "No faulty chunks found for telescope 'tel_id=%d'. Skipping second pass.",
                        tel_id,
                    )
            # Write the aggregated statistics and their outlier mask to the output file
            write_table(
                aggregated_stats,
                self.output_path,
                f"/dl1/monitoring/telescope/{self.output_column_name}/tel_{tel_id:03d}",
                overwrite=self.overwrite,
            )

    def finish(self):
        self.log.info(
            "DL1 monitoring data was stored in '%s' under '%s'",
            self.output_path,
            f"/dl1/monitoring/telescope/{self.output_column_name}",
        )
        self.log.info("Tool is shutting down")


def main():
    # Run the tool
    tool = StatisticsCalculatorTool()
    tool.run()


if __name__ == "main":
    main()
