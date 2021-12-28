#!/usr/bin/env python
"""
Script to populate the first employed date field on officers.

This uses the assignments.csv file which can be downloaded from from OO directly.
"""
import logging
from pathlib import Path

import click
import pandas as pd


log = logging.getLogger()


def get_first_employed(
    assignments: pd.DataFrame, space_in_name: bool = True
) -> pd.DataFrame:
    officer_id = "officer id" if space_in_name else "officer_id"
    start_date = "start date" if space_in_name else "start_date"
    # Sort input CSV first by officer, then by start date (ascending).
    # Group by officer, and grab the first row. This will have their first assignment
    # date on record.
    first_employed = (
        assignments.sort_values(by=[officer_id, start_date]).groupby(officer_id).first()
    )
    # The index is now officer id, so we only need to keep the start date column.
    # (double brackets here so the entity remains a dataframe and not a series)
    first_employed = first_employed[[start_date]]
    # Add the department name
    first_employed["department_name"] = "Seattle Police Department"
    # Rename to the fields OO is expecting
    first_employed = first_employed.reset_index().rename(
        {officer_id: "id", start_date: "employment_date"}, axis="columns"
    )
    return first_employed


def main(assignment_path: Path, output: Path):
    log.info("Starting import")
    assignments = pd.read_csv(assignment_path)
    first_employed = get_first_employed(assignments)
    # Save!
    first_employed.to_csv(output, index=False)
    log.info("Finished")


@click.command()
@click.argument("assignment_path", type=click.Path(exists=True, path_type=Path))
@click.argument("output", type=click.Path(path_type=Path))
def cli(assignment_path: Path, output: Path):
    logging.basicConfig(
        format="[%(asctime)s - %(name)s - %(lineno)3d][%(levelname)s] %(message)s",
        level=logging.INFO,
    )
    main(assignment_path, output)


if __name__ == "__main__":
    cli()
