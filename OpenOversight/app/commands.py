import csv
import sys
from builtins import input
from datetime import date, datetime
from decimal import Decimal
from getpass import getpass
from typing import Dict, List

import click
import us
from dateutil.parser import parse
from flask import current_app
from flask.cli import with_appcontext

from OpenOversight.app.csv_imports import import_csv_files
from OpenOversight.app.models.database import (
    Assignment,
    Department,
    Face,
    Image,
    Job,
    Officer,
    Salary,
    Unit,
    User,
    db,
)
from OpenOversight.app.utils.constants import ENCODING_UTF_8
from OpenOversight.app.utils.db import get_officer
from OpenOversight.app.utils.general import normalize_gender, prompt_yes_no, str_is_true


@click.command()
@click.option(
    "-u",
    "--username",
    "supplied_username",
    help="username for the admin account",
)
@click.option(
    "-e",
    "--email",
    "supplied_email",
    help="email for the admin account",
)
@click.option(
    "-p",
    "--password",
    "supplied_password",
    help="password for the admin account",
)
@with_appcontext
def make_admin_user(
    supplied_username: str | None,
    supplied_email: str | None,
    supplied_password: str | None,
):
    """Add confirmed administrator account."""
    if supplied_username:
        username = supplied_username
    else:
        while True:
            username = input("Username: ")
            user = User.by_username(username).one_or_none()
            if user:
                print("Username is already in use")
            else:
                break

    if supplied_email:
        email = supplied_email
    else:
        while True:
            email = input("Email: ")
            user = User.by_email(email).one_or_none()
            if user:
                print("Email address already in use")
            else:
                break

    if supplied_password:
        password = supplied_password
    else:
        while True:
            password = getpass("Password: ")
            password_again = getpass("Type your password again: ")

            if password == password_again:
                break
            print("Passwords did not match")

    u = User(
        username=username,
        email=email,
        password=password,
        is_administrator=True,
    )
    db.session.add(u)
    db.session.flush()

    u.confirmed_at = datetime.now()
    u.confirmed_by = u.id
    db.session.commit()
    print(f"Administrator {username} successfully added")
    current_app.logger.info(f"Administrator {username} added with email {email}")


@click.command()
@with_appcontext
def link_images_to_department():
    """Link existing images to first department."""
    images = Image.query.all()
    print("Linking images to first department:")
    for image in images:
        if not image.department_id:
            sys.stdout.write(".")
            image.department_id = 1
        else:
            print("Skipped! Department already assigned")
    db.session.commit()


@click.command()
@with_appcontext
def link_officers_to_department():
    """Links officers and unit_ids to first department."""
    officers = Officer.query.all()
    units = Unit.query.all()

    print("Linking officers and units to first department:")
    for item in officers + units:
        if not item.department_id:
            sys.stdout.write(".")
            item.department_id = 1
        else:
            print("Skipped! Object already assigned to department!")
    db.session.commit()


class ImportLog:
    updated_officers: Dict[int, List] = {}
    created_officers: Dict[int, List] = {}

    @classmethod
    def log_change(cls, officer, msg):
        if officer.id not in cls.created_officers:
            if officer.id not in cls.updated_officers:
                cls.updated_officers[officer.id] = []
            log = cls.updated_officers[officer.id]
        else:
            log = cls.created_officers[officer.id]
        log.append(msg)

    @classmethod
    def log_new_officer(cls, officer):
        cls.created_officers[officer.id] = []

    @classmethod
    def print_create_logs(cls):
        officers = Officer.query.filter(
            Officer.id.in_(cls.created_officers.keys())
        ).all()
        for officer in officers:
            print(f"Created officer {officer}")
            for msg in cls.created_officers[officer.id]:
                print(" --->", msg)

    @classmethod
    def print_update_logs(cls):
        officers = Officer.query.filter(
            Officer.id.in_(cls.updated_officers.keys())
        ).all()
        for officer in officers:
            print(f"Updates to officer {officer}:")
            for msg in cls.updated_officers[officer.id]:
                print(" --->", msg)

    @classmethod
    def print_logs(cls):
        cls.print_create_logs()
        cls.print_update_logs()

    @classmethod
    def clear_logs(cls):
        cls.updated_officers = {}
        cls.created_officers = {}


def row_has_data(row, required_fields, optional_fields):
    for field in required_fields:
        if field not in row or not row[field]:
            return False
    n_optional = 0
    for field in optional_fields:
        if field in row and row[field]:
            n_optional += 1
    if len(required_fields) > 0 or n_optional > 0:
        return True
    return False


def set_field_from_row(row, obj, attribute, allow_blank=True, field_name=None):
    field_name = field_name or attribute
    if field_name in row and (row[field_name] or allow_blank):
        try:
            val = datetime.strptime(row[field_name], "%Y-%m-%d").date()
        except ValueError:
            val = row[field_name]
            if attribute == "gender":
                val = normalize_gender(val)
        setattr(obj, attribute, val)


def update_officer_from_row(row, officer, update_static_fields=False):
    def update_officer_field(officer_field_name):
        if officer_field_name not in row:
            return

        if officer_field_name == "gender":
            row[officer_field_name] = normalize_gender(row[officer_field_name])

        if (
            row[officer_field_name]
            and getattr(officer, officer_field_name) != row[officer_field_name]
        ):
            ImportLog.log_change(
                officer,
                f"Updated {officer_field_name}: {getattr(officer, officer_field_name)} --> {row[officer_field_name]}",
            )
            setattr(officer, officer_field_name, row[officer_field_name])

    # Name and gender are the only potentially changeable fields, so update those
    update_officer_field("last_name")
    update_officer_field("first_name")
    update_officer_field("middle_initial")

    update_officer_field("suffix")
    update_officer_field("gender")

    # The rest should be static
    static_fields = [
        "unique_internal_identifier",
        "race",
        "employment_date",
        "birth_year",
    ]
    for field_name in static_fields:
        if field_name in row:
            if row[field_name] == "":
                row[field_name] = None
            old_value = getattr(officer, field_name)
            # If we're expecting a date type, attempt to parse row[field_name] as a
            # datetime. This normalizes all date formats, ensuring the following
            # comparison works properly
            if isinstance(old_value, (date, datetime)):
                try:
                    new_value = parse(row[field_name])
                    if isinstance(old_value, date):
                        new_value = new_value.date()
                except Exception as err:
                    msg = (
                        f'Field {field_name} is a date-type, but "{row[field_name]}"'
                        f" was specified for Officer {officer.first_name} "
                        f"{officer.last_name} and cannot be parsed as a "
                        f"date-type.\nError message from dateutil: {err}"
                    )
                    raise Exception(msg) from err
            else:
                new_value = row[field_name]
            if old_value is None:
                update_officer_field(field_name)
            elif str(old_value) != str(new_value):
                msg = (
                    f"Officer {officer.first_name} {officer.last_name} has "
                    f"differing {field_name} field. Old: {old_value}, "
                    f"new: {new_value}"
                )
                if update_static_fields:
                    print(msg)
                    update_officer_field(field_name)
                else:
                    raise Exception(msg)

    process_assignment(row, officer, compare=True)
    process_salary(row, officer, compare=True)


def create_officer_from_row(row, department_id):
    officer = Officer()
    officer.department_id = department_id

    set_field_from_row(row, officer, "last_name", allow_blank=False)
    set_field_from_row(row, officer, "first_name", allow_blank=False)
    set_field_from_row(row, officer, "middle_initial")
    set_field_from_row(row, officer, "suffix")
    set_field_from_row(row, officer, "race")
    set_field_from_row(row, officer, "gender")
    set_field_from_row(row, officer, "employment_date", allow_blank=False)
    set_field_from_row(row, officer, "birth_year")
    set_field_from_row(row, officer, "unique_internal_identifier")
    db.session.add(officer)
    db.session.flush()

    ImportLog.log_new_officer(officer)

    process_assignment(row, officer, compare=False)
    process_salary(row, officer, compare=False)


def is_equal(a, b):
    """Run an exhaustive equality check, originally to compare a sqlalchemy result
    object of various types to a csv string.
    Note: Stringifying covers object cases (as in the datetime example below)
    >>> is_equal("1", 1)  # string == int
    True
    >>> is_equal("foo", "bar") # string != other string
    False
    >>> is_equal(1, "1") # int == string
    True
    >>> is_equal(1.0, "1") # float == string
    True
    >>> is_equal(datetime(2020, 1, 1), "2020-01-01 00:00:00") # datetime == string
    True
    """

    def try_else_false(comparable):
        try:
            return comparable(a, b)
        except TypeError:
            return False
        except ValueError:
            return False

    return any(
        [
            try_else_false(lambda _a, _b: str(_a) == str(_b)),
            try_else_false(lambda _a, _b: int(_a) == int(_b)),
            try_else_false(lambda _a, _b: float(_a) == float(_b)),
        ]
    )


def process_assignment(row, officer, compare=False):
    assignment_fields = {
        "required": [],
        "optional": ["job_title", "star_no", "unit_id", "start_date", "resign_date"],
    }

    # See if the row has assignment data
    if row_has_data(row, assignment_fields["required"], assignment_fields["optional"]):
        add_assignment = True
        if compare:
            # Get existing assignments for officer and compare to row data
            assignments = (
                db.session.query(Assignment, Job)
                .filter(Assignment.job_id == Job.id)
                .filter_by(officer_id=officer.id)
                .all()
            )
            for assignment, job in assignments:
                assignment_field_names = [
                    "star_no",
                    "unit_id",
                    "start_date",
                    "resign_date",
                ]
                i = 0
                for field_name in assignment_field_names:
                    current = getattr(assignment, field_name)
                    # Test if fields match between row and existing assignment
                    if (
                        current
                        and field_name in row
                        and is_equal(row[field_name], current)
                    ) or (
                        not current and (field_name not in row or not row[field_name])
                    ):
                        i += 1
                if i == len(assignment_field_names):
                    job_title = job.job_title
                    if (
                        job_title and row.get("job_title", "Not Sure") == job_title
                    ) or (
                        not job_title
                        and ("job_title" not in row or not row["job_title"])
                    ):
                        # Found match, so don't add new assignment
                        add_assignment = False
        if add_assignment:
            job = Job.query.filter_by(
                job_title=row.get("job_title", "Not Sure"),
                department_id=officer.department_id,
            ).one_or_none()
            if not job:
                num_existing_ranks = len(
                    Job.query.filter_by(department_id=officer.department_id).all()
                )
                if num_existing_ranks > 0:
                    auto_order = num_existing_ranks + 1
                else:
                    auto_order = 0
                # create new job
                job = Job(
                    is_sworn_officer=False,
                    department_id=officer.department_id,
                    order=auto_order,
                )
                set_field_from_row(row, job, "job_title", allow_blank=False)
                db.session.add(job)
                db.session.flush()
            # create new assignment
            assignment = Assignment()
            assignment.officer_id = officer.id
            assignment.job_id = job.id
            set_field_from_row(row, assignment, "star_no")
            set_field_from_row(row, assignment, "unit_id")
            set_field_from_row(row, assignment, "start_date", allow_blank=False)
            set_field_from_row(row, assignment, "resign_date", allow_blank=False)
            db.session.add(assignment)
            db.session.flush()

            ImportLog.log_change(officer, f"Added assignment: {assignment}")


def process_salary(row, officer, compare=False):
    salary_fields = {
        "required": ["salary", "salary_year", "salary_is_fiscal_year"],
        "optional": ["overtime_pay"],
    }

    # See if the row has salary data
    if row_has_data(row, salary_fields["required"], salary_fields["optional"]):
        is_fiscal_year = str_is_true(row["salary_is_fiscal_year"])

        add_salary = True
        if compare:
            # Get existing salaries for officer and compare to row data
            salaries = Salary.query.filter_by(officer_id=officer.id).all()
            for salary in salaries:
                print(vars(salary))
                print(row)
                if (
                    Decimal(f"{salary.salary:.2f}")
                    == Decimal(f"{float(row['salary']):.2f}")
                    and salary.year == int(row["salary_year"])
                    and salary.is_fiscal_year == is_fiscal_year
                    and (
                        (
                            salary.overtime_pay
                            and "overtime_pay" in row
                            and Decimal(f"{salary.overtime_pay:.2f}")
                            == Decimal(f"{float(row['overtime_pay']):.2f}")
                        )
                        or (
                            not salary.overtime_pay
                            and ("overtime_pay" not in row or not row["overtime_pay"])
                        )
                    )
                ):
                    # Found match, so don't add new salary
                    add_salary = False

        if add_salary:
            # create new salary
            salary = Salary(
                officer_id=officer.id,
                salary=round(Decimal(row["salary"]), 2),
                year=int(row["salary_year"]),
                is_fiscal_year=is_fiscal_year,
            )
            if "overtime_pay" in row and row["overtime_pay"]:
                salary.overtime_pay = round(Decimal(row["overtime_pay"]), 2)
            db.session.add(salary)
            db.session.flush()

            ImportLog.log_change(officer, f"Added salary: {salary}")


@click.command()
@click.argument("filename")
@click.option(
    "--no-create", is_flag=True, help="only update officers; do not create new ones"
)
@click.option(
    "--update-by-name",
    is_flag=True,
    help="update officers by first and last name (useful when star_no or "
    "unique_internal_identifier are not available)",
)
@click.option(
    "--update-static-fields",
    is_flag=True,
    help="allow updating normally-static fields like race, birth year, etc.",
)
@click.option(
    "--yes",
    "-y",
    "bypass_prompt",
    is_flag=True,
    help="bypass the user prompt and immediately add the officers to the database",
)
@with_appcontext
def bulk_add_officers(
    filename, no_create, update_by_name, update_static_fields, bypass_prompt
):
    """Add or update officers from a CSV file."""

    encoding = ENCODING_UTF_8

    # handles unicode errors that can occur when the file was made in Excel
    with open(filename, "r") as f:
        if "\ufeff" in f.readline():
            encoding = "utf-8-sig"

    with open(filename, "r", encoding=encoding) as f:
        ImportLog.clear_logs()
        csvfile = csv.DictReader(f)
        departments = {}

        required_fields = [
            "department_id",
            "first_name",
            "last_name",
        ]

        # Assert required fields are in CSV file
        for field in required_fields:
            if field not in csvfile.fieldnames:
                raise Exception(f"Missing required field {field}")
        if (
            not update_by_name
            and "star_no" not in csvfile.fieldnames
            and "unique_internal_identifier" not in csvfile.fieldnames
        ):
            raise Exception(
                "CSV file must include either badge numbers or unique identifiers for "
                "officers"
            )

        for row in csvfile:
            department_id = row["department_id"]
            department = departments.get(department_id)
            if row["department_id"] not in departments:
                department = db.session.get(Department, department_id)
                if department:
                    departments[department_id] = department
                else:
                    raise Exception(f"Department ID {department_id} not found")

            if not update_by_name:
                # Check for existing officer based on unique ID or name/badge
                if (
                    "unique_internal_identifier" in csvfile.fieldnames
                    and row["unique_internal_identifier"]
                ):
                    officer = Officer.query.filter_by(
                        department_id=department_id,
                        unique_internal_identifier=row["unique_internal_identifier"],
                    ).one_or_none()
                elif "star_no" in csvfile.fieldnames and row["star_no"]:
                    officer = get_officer(
                        department_id,
                        row["star_no"],
                        row["first_name"],
                        row["last_name"],
                    )
                else:
                    raise Exception(
                        f"Officer {row['first_name']} {row['last_name']} "
                        "missing badge number and unique identifier"
                    )
            else:
                officer = Officer.query.filter_by(
                    department_id=department_id,
                    last_name=row["last_name"],
                    first_name=row["first_name"],
                ).one_or_none()

            if officer:
                update_officer_from_row(row, officer, update_static_fields)
            elif not no_create:
                create_officer_from_row(row, department_id)

        ImportLog.print_logs()
        if (
            current_app.config["ENV"] == "testing"
            or bypass_prompt
            or prompt_yes_no("Do you want to commit the above changes?")
        ):
            print("Commiting changes.")
            db.session.commit()
        else:
            print("Aborting changes.")
            db.session.rollback()
            return 0, 0

        return len(ImportLog.created_officers), len(ImportLog.updated_officers)


@click.command()
@click.argument("department-name", required=True)
@click.argument(
    "department-state",
    type=click.Choice([state.abbr for state in us.STATES]),
    required=True,
)
@click.option("--officers-csv", type=click.Path(exists=True))
@click.option("--assignments-csv", type=click.Path(exists=True))
@click.option("--salaries-csv", type=click.Path(exists=True))
@click.option("--links-csv", type=click.Path(exists=True))
@click.option("--incidents-csv", type=click.Path(exists=True))
@click.option("--force-create", is_flag=True, help="Only for development/testing!")
@click.option("--overwrite-assignments", is_flag=True)
@with_appcontext
def advanced_csv_import(
    department_name,
    department_state,
    officers_csv,
    assignments_csv,
    salaries_csv,
    links_csv,
    incidents_csv,
    force_create,
    overwrite_assignments,
):
    """
    Add or update officers, assignments, salaries, links and incidents from
    csv files in the department using the DEPARTMENT_NAME and DEPARTMENT_STATE.

    The csv files are treated as the source of truth.
    Existing entries might be overwritten as a result, backing up the
    database and running the command locally first is highly recommended.

    See the documentation before running the command.
    """
    if force_create and current_app.config["ENV"] == "production":
        raise Exception("--force-create cannot be used in production!")

    import_csv_files(
        department_name,
        department_state,
        officers_csv,
        assignments_csv,
        salaries_csv,
        links_csv,
        incidents_csv,
        force_create,
        overwrite_assignments,
    )


@click.command()
@click.argument("name", required=True)
@click.argument("short_name", required=True)
@click.argument(
    "state", type=click.Choice([state.abbr for state in us.STATES]), required=True
)
@click.argument("unique_internal_identifier", required=False)
@with_appcontext
def add_department(name, short_name, state, unique_internal_identifier):
    """Add a new department to OpenOversight."""
    dept = Department(
        name=name,
        short_name=short_name,
        state=state.upper(),
        unique_internal_identifier_label=unique_internal_identifier,
    )
    db.session.add(dept)
    db.session.commit()
    print(f"Department added with id {dept.id}")


@click.command()
@click.argument("department_id")
@click.argument("job_title")
@click.argument(
    "is_sworn_officer", type=click.Choice(["true", "false"], case_sensitive=False)
)
@click.argument("order", type=int)
@with_appcontext
def add_job_title(department_id, job_title, is_sworn_officer, order):
    """Add a rank to a department."""
    department = db.session.get(Department, department_id)
    is_sworn = is_sworn_officer == "true"
    job = Job(
        job_title=job_title,
        is_sworn_officer=is_sworn,
        order=order,
        department=department,
    )
    db.session.add(job)
    print(f"Added {job.job_title} to {department.name}")
    db.session.commit()


@click.command()
@with_appcontext
def use_original_image_for_faces():
    """Migrate created Faces to use the original image ID instead of the cropped one."""
    faces = Face.query.all()
    for face in faces:
        face.img_id = face.original_image_id
    db.session.commit()
