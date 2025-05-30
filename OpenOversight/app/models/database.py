import itertools
import operator
import re
import time
import uuid
from datetime import date, datetime
from datetime import time as dt_time
from datetime import timezone
from decimal import Decimal
from typing import List, Optional

from authlib.jose import JoseError, JsonWebToken
from cachetools import cached
from flask import current_app
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, func
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import (
    DeclarativeMeta,
    contains_eager,
    declarative_mixin,
    declared_attr,
    joinedload,
    validates,
)
from sqlalchemy.sql import func as sql_func
from sqlalchemy.types import TypeDecorator
from werkzeug.security import check_password_hash, generate_password_hash

from OpenOversight.app.models.database_cache import (
    DB_CACHE,
    get_database_cache_entry,
    model_cache_key,
    put_database_cache_entry,
    remove_database_cache_entries,
)
from OpenOversight.app.utils.choices import GENDER_CHOICES, RACE_CHOICES
from OpenOversight.app.utils.constants import (
    ENCODING_UTF_8,
    KEY_DB_CREATOR,
    KEY_DEPT_ALL_ASSIGNMENTS,
    KEY_DEPT_ALL_INCIDENTS,
    KEY_DEPT_ALL_LINKS,
    KEY_DEPT_ALL_NOTES,
    KEY_DEPT_ALL_OFFICERS,
    KEY_DEPT_ALL_SALARIES,
    KEY_DEPT_ASSIGNMENTS_LAST_UPDATED,
    KEY_DEPT_INCIDENTS_LAST_UPDATED,
    KEY_DEPT_OFFICERS_LAST_UPDATED,
    SIGNATURE_ALGORITHM,
)
from OpenOversight.app.validators import state_validator, url_validator


db = SQLAlchemy()
jwt = JsonWebToken(SIGNATURE_ALGORITHM)
Base: DeclarativeMeta = db.Model


class TZDateTime(TypeDecorator):
    """
    Store tz-aware datetimes as tz-naive UTC datetimes in sqlite.
    https://docs.sqlalchemy.org/en/20/core/custom_types.html#store-timezone-aware-timestamps-as-timezone-naive-utc
    """

    cache_ok = True
    impl = db.DateTime

    def process_bind_param(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            if not value.tzinfo or value.tzinfo.utcoffset(value) is None:
                raise TypeError("tzinfo is required")
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class BaseModel(Base):
    __abstract__ = True

    EXCLUDED = [
        "approved_at",
        "approved_by",
        "confirmed_at",
        "confirmed_by",
        "created_at",
        "created_by",
        "disabled_at",
        "disabled_by",
        "password_hash",
        "last_updated_at",
        "last_updated_by",
    ]

    def __repr__(self) -> str:
        """Convert model to a string that contains all values needed for recreation."""
        ret_str = f"<{self.__class__.__name__} ("
        for column in inspect(self).mapper.column_attrs:
            if column.key in self.EXCLUDED or column.key.startswith("_"):
                continue

            if ret_str[-1] != "(":
                ret_str += " : "

            value = getattr(self, column.key)
            if isinstance(value, (date, datetime)):
                ret_str += f"{column.key}: {value.isoformat()}"
            elif isinstance(value, date):
                ret_str += f'{column.key}: {value.strftime("%Y-%m-%d")}'
            elif isinstance(value, dt_time):
                ret_str += f'{column.key}: {value.strftime("%I:%M %p")}'
            else:
                ret_str += f"{column.key}: {value}"

        return ret_str + ")>"

    def to_dict(self) -> dict:
        """Convert a generic model instance into a dictionary."""
        data = {}

        for column in inspect(self).mapper.column_attrs:
            if column.key in self.EXCLUDED or column.key.startswith("_"):
                continue

            value = getattr(self, column.key)
            if isinstance(value, (date, datetime)):
                data[column.key] = value.isoformat()
            elif isinstance(value, date):
                data[column.key] = value.strftime("%Y-%m-%d")
            elif isinstance(value, dt_time):
                data[column.key] = value.strftime("%I:%M %p")
            else:
                data[column.key] = value

        return data


officer_links = db.Table(
    "officer_links",
    db.Column(
        "officer_id",
        db.Integer,
        db.ForeignKey("officers.id", name="officer_links_officer_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "link_id",
        db.Integer,
        db.ForeignKey("links.id", name="officer_links_link_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "created_at",
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    ),
)

officer_incidents = db.Table(
    "officer_incidents",
    db.Column(
        "officer_id",
        db.Integer,
        db.ForeignKey("officers.id", name="officer_incidents_officer_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "incident_id",
        db.Integer,
        db.ForeignKey("incidents.id", name="officer_incidents_incident_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "created_at",
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    ),
)


@declarative_mixin
class TrackUpdates:
    """Add columns to track the date of and user who created and last modified
    the object.
    """

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    )
    last_updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
        onupdate=datetime.utcnow,
    )

    @declared_attr
    def created_by(cls):
        return db.Column(
            db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), unique=False
        )

    @declared_attr
    def last_updated_by(cls):
        return db.Column(
            db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), unique=False
        )

    @declared_attr
    def creator(cls):
        return db.relationship("User", foreign_keys=[cls.created_by])


class Department(BaseModel, TrackUpdates):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), index=False, unique=False, nullable=False)
    short_name = db.Column(db.String(100), unique=False, nullable=False)
    state = db.Column(db.String(2), server_default="", nullable=False)

    # See https://github.com/lucyparsons/OpenOversight/issues/462
    unique_internal_identifier_label = db.Column(
        db.String(100), unique=False, nullable=True
    )

    __table_args__ = (UniqueConstraint("name", "state", name="departments_name_state"),)

    @cached(cache=DB_CACHE, key=model_cache_key(KEY_DEPT_ASSIGNMENTS_LAST_UPDATED))
    def latest_assignment_update(self) -> date:
        assignment_updated = (
            db.session.query(func.max(Assignment.last_updated_at))
            .join(Officer)
            .filter(Assignment.officer_id == Officer.id)
            .filter(Officer.department_id == self.id)
            .scalar()
        )
        return assignment_updated.date() if assignment_updated else None

    @staticmethod
    def get_assignments(department_id: int) -> list["Assignment"]:
        cache_params = Department(id=department_id), KEY_DEPT_ALL_ASSIGNMENTS
        assignments = get_database_cache_entry(*cache_params)

        if assignments is None:
            assignments = (
                db.session.query(Assignment)
                .join(Assignment.base_officer)
                .filter(Officer.department_id == department_id)
                .options(contains_eager(Assignment.base_officer))
                .options(joinedload(Assignment.unit))
                .options(joinedload(Assignment.job))
                .all()
            )
            put_database_cache_entry(*cache_params, assignments)

        return assignments

    @staticmethod
    def get_descriptions(department_id: int) -> list["Description"]:
        cache_params = (Department(id=department_id), KEY_DEPT_ALL_NOTES)
        descriptions = get_database_cache_entry(*cache_params)

        if descriptions is None:
            descriptions = (
                db.session.query(Description)
                .join(Description.officer)
                .filter(Officer.department_id == department_id)
                .options(contains_eager(Description.officer))
                .all()
            )
            put_database_cache_entry(*cache_params, descriptions)

        return descriptions

    @staticmethod
    def get_incidents(department_id: int) -> list["Incident"]:
        cache_params = (Department(id=department_id), KEY_DEPT_ALL_INCIDENTS)
        incidents = get_database_cache_entry(*cache_params)

        if incidents is None:
            incidents = Incident.query.filter_by(department_id=department_id).all()
            put_database_cache_entry(*cache_params, incidents)

        return incidents

    @staticmethod
    def get_links(department_id: int) -> list["Link"]:
        cache_params = (Department(id=department_id), KEY_DEPT_ALL_LINKS)
        links = get_database_cache_entry(*cache_params)

        if links is None:
            links = (
                db.session.query(Link)
                .join(Link.officers)
                .filter(Officer.department_id == department_id)
                .options(contains_eager(Link.officers))
                .all()
            )
            put_database_cache_entry(*cache_params, links)

        return links

    @staticmethod
    def get_officers(department_id: int) -> list["Officer"]:
        cache_params = (Department(id=department_id), KEY_DEPT_ALL_OFFICERS)
        officers = get_database_cache_entry(*cache_params)

        if officers is None:
            officers = (
                db.session.query(Officer)
                .options(joinedload(Officer.assignments).joinedload(Assignment.job))
                .options(joinedload(Officer.salaries))
                .filter_by(department_id=department_id)
                .all()
            )
            put_database_cache_entry(*cache_params, officers)

        return officers

    @staticmethod
    def get_salaries(department_id: int) -> list["Salary"]:
        cache_params = (Department(id=department_id), KEY_DEPT_ALL_SALARIES)
        salaries = get_database_cache_entry(*cache_params)

        if salaries is None:
            salaries = (
                db.session.query(Salary)
                .join(Salary.officer)
                .filter(Officer.department_id == department_id)
                .options(contains_eager(Salary.officer))
                .all()
            )
            put_database_cache_entry(*cache_params, salaries)

        return salaries

    @cached(cache=DB_CACHE, key=model_cache_key(KEY_DEPT_INCIDENTS_LAST_UPDATED))
    def latest_incident_update(self) -> date:
        incident_updated = (
            db.session.query(func.max(Incident.last_updated_at))
            .filter(Incident.department_id == self.id)
            .scalar()
        )
        return incident_updated.date() if incident_updated else None

    @cached(cache=DB_CACHE, key=model_cache_key(KEY_DEPT_OFFICERS_LAST_UPDATED))
    def latest_officer_update(self) -> date:
        officer_updated = (
            db.session.query(func.max(Officer.last_updated_at))
            .filter(Officer.department_id == self.id)
            .scalar()
        )
        return officer_updated.date() if officer_updated else None

    def remove_database_cache_entries(self, update_types: List[str]) -> None:
        """Remove the Department model key from the cache if it exists."""
        remove_database_cache_entries(self, update_types)

    @staticmethod
    def by_state() -> dict[str, list["Department"]]:
        departments = Department.query.filter(Department.officers.any()).order_by(
            Department.state.asc(), Department.name.asc()
        )
        departments_by_state = {
            state: list(group)
            for state, group in itertools.groupby(departments, lambda d: d.state)
        }
        return departments_by_state


class Job(BaseModel, TrackUpdates):
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    job_title = db.Column(db.String(255), index=True, unique=False, nullable=False)
    is_sworn_officer = db.Column(db.Boolean, index=True, default=True)
    order = db.Column(db.Integer, index=True, unique=False, nullable=False)
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id", name="jobs_department_id_fkey")
    )
    department = db.relationship(
        "Department", backref=db.backref("jobs", cascade_backrefs=False)
    )

    __table_args__ = (
        UniqueConstraint(
            "job_title", "department_id", name="unique_department_job_titles"
        ),
    )

    def __str__(self):
        return self.job_title


class Note(BaseModel, TrackUpdates):
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    text_contents = db.Column(db.Text())
    officer_id = db.Column(db.Integer, db.ForeignKey("officers.id", ondelete="CASCADE"))
    officer = db.relationship("Officer", back_populates="notes")


class Description(BaseModel, TrackUpdates):
    __tablename__ = "descriptions"

    id = db.Column(db.Integer, primary_key=True)
    text_contents = db.Column(db.Text())
    officer_id = db.Column(db.Integer, db.ForeignKey("officers.id", ondelete="CASCADE"))
    officer = db.relationship("Officer", back_populates="descriptions")


class Officer(BaseModel, TrackUpdates):
    __tablename__ = "officers"

    id = db.Column(db.Integer, primary_key=True)
    last_name = db.Column(db.String(120), index=True, unique=False)
    first_name = db.Column(db.String(120), index=True, unique=False)
    middle_initial = db.Column(db.String(120), unique=False, nullable=True)
    suffix = db.Column(db.String(120), index=True, unique=False)
    race = db.Column(db.String(120), index=True, unique=False)
    gender = db.Column(db.String(5), index=True, unique=False, nullable=True)
    employment_date = db.Column(db.Date, index=True, unique=False, nullable=True)
    birth_year = db.Column(db.Integer, index=True, unique=False, nullable=True)
    assignments = db.relationship(
        "Assignment", back_populates="base_officer", cascade_backrefs=False
    )
    face = db.relationship(
        "Face", backref=db.backref("officer", cascade_backrefs=False)
    )
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id", name="officers_department_id_fkey")
    )
    department = db.relationship(
        "Department", backref=db.backref("officers", cascade_backrefs=False)
    )
    unique_internal_identifier = db.Column(
        db.String(50), index=True, unique=True, nullable=True
    )

    links = db.relationship(
        "Link",
        secondary=officer_links,
        backref=db.backref("officers", lazy=True, cascade_backrefs=False),
        lazy=True,
    )
    notes = db.relationship(
        "Note",
        back_populates="officer",
        cascade_backrefs=False,
        order_by="Note.created_at",
    )
    descriptions = db.relationship(
        "Description",
        back_populates="officer",
        cascade_backrefs=False,
        order_by="Description.created_at",
    )
    salaries = db.relationship(
        "Salary",
        back_populates="officer",
        cascade_backrefs=False,
        order_by="Salary.year.desc()",
    )

    __table_args__ = (
        CheckConstraint("gender in ('M', 'F', 'Other')", name="gender_options"),
    )

    def __repr__(self):
        if self.unique_internal_identifier:
            return (
                f"<Officer ID: {self.id} : {self.full_name()} "
                f"({self.unique_internal_identifier})>"
            )
        return f"<Officer ID: {self.id} : {self.full_name()}>"

    def full_name(self):
        if self.middle_initial:
            middle_initial = (
                self.middle_initial + "."
                if len(self.middle_initial) == 1
                else self.middle_initial
            )
            if self.suffix:
                return (
                    f"{self.first_name} {middle_initial} {self.last_name} {self.suffix}"
                )
            else:
                return f"{self.first_name} {middle_initial} {self.last_name}"
        if self.suffix:
            return f"{self.first_name} {self.last_name} {self.suffix}"
        return f"{self.first_name} {self.last_name}"

    def race_label(self):
        if self.race is None:
            return "Data Missing"

        for race, label in RACE_CHOICES:
            if self.race == race:
                return label

    def gender_label(self):
        if self.gender is None:
            return "Data Missing"

        for gender, label in GENDER_CHOICES:
            if self.gender == gender:
                return label

    def job_title(self):
        if self.assignments:
            return max(
                self.assignments, key=operator.attrgetter("start_date_or_min")
            ).job.job_title

    def unit_description(self):
        if self.assignments:
            unit = max(
                self.assignments, key=operator.attrgetter("start_date_or_min")
            ).unit
            return unit.description if unit else None

    def badge_number(self):
        if self.assignments:
            return max(
                self.assignments, key=operator.attrgetter("start_date_or_min")
            ).star_no

    def currently_on_force(self):
        if self.assignments:
            most_recent = max(
                self.assignments, key=operator.attrgetter("start_date_or_min")
            )
            return "Yes" if most_recent.resign_date is None else "No"
        return "Uncertain"


class Currency(TypeDecorator):
    """
    Store currency as an integer in sqlite to avoid float conversion
    https://stackoverflow.com/questions/10355767/
    """

    impl = db.Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):
        typ = db.Numeric()
        if dialect.name == "sqlite":
            typ = db.Integer()
        return dialect.type_descriptor(typ)

    def process_bind_param(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            value = int(Decimal(value) * 100)
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            value = Decimal(value) / 100
        return value


class Salary(BaseModel, TrackUpdates):
    __tablename__ = "salaries"

    id = db.Column(db.Integer, primary_key=True)
    officer_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "officers.id", name="salaries_officer_id_fkey", ondelete="CASCADE"
        ),
    )
    officer = db.relationship("Officer", back_populates="salaries")
    salary = db.Column(Currency(), index=True, unique=False, nullable=False)
    overtime_pay = db.Column(Currency(), index=True, unique=False, nullable=True)
    year = db.Column(db.Integer, index=True, unique=False, nullable=False)
    is_fiscal_year = db.Column(db.Boolean, index=False, unique=False, nullable=False)

    @property
    def total_pay(self) -> float:
        return self.salary + self.overtime_pay

    @property
    def year_repr(self) -> str:
        if self.is_fiscal_year:
            return f"FY{self.year}"
        return str(self.year)


class Assignment(BaseModel, TrackUpdates):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    officer_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "officers.id", name="assignments_officer_id_fkey", ondelete="CASCADE"
        ),
    )
    base_officer = db.relationship("Officer", back_populates="assignments")
    star_no = db.Column(db.String(120), index=True, unique=False, nullable=True)
    job_id = db.Column(
        db.Integer,
        db.ForeignKey("jobs.id", name="assignments_job_id_fkey"),
        nullable=False,
    )
    job = db.relationship("Job")
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey("unit_types.id", name="assignments_unit_id_fkey"),
        nullable=True,
    )
    unit = db.relationship("Unit")
    start_date = db.Column(db.Date, index=True, unique=False, nullable=True)
    resign_date = db.Column(db.Date, index=True, unique=False, nullable=True)

    @property
    def start_date_or_min(self):
        return self.start_date or date.min

    @property
    def start_date_or_max(self):
        return self.start_date or date.max


class Unit(BaseModel, TrackUpdates):
    __tablename__ = "unit_types"

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(120), index=True, unique=False)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="unit_types_department_id_fkey"),
    )
    department = db.relationship(
        "Department",
        backref=db.backref("unit_types", cascade_backrefs=False),
        order_by="Unit.description.asc()",
    )


class Face(BaseModel, TrackUpdates):
    __tablename__ = "faces"

    id = db.Column(db.Integer, primary_key=True)
    officer_id = db.Column(
        db.Integer, db.ForeignKey("officers.id", name="faces_officer_id_fkey")
    )
    img_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "raw_images.id",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_face_image_id",
            use_alter=True,
        ),
    )
    original_image_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "raw_images.id",
            ondelete="SET NULL",
            onupdate="CASCADE",
            use_alter=True,
            name="fk_face_original_image_id",
        ),
    )
    face_position_x = db.Column(db.Integer, unique=False)
    face_position_y = db.Column(db.Integer, unique=False)
    face_width = db.Column(db.Integer, unique=False)
    face_height = db.Column(db.Integer, unique=False)
    image = db.relationship(
        "Image",
        backref=db.backref("faces", cascade_backrefs=False),
        foreign_keys=[img_id],
    )
    original_image = db.relationship(
        "Image",
        backref=db.backref("tags", cascade_backrefs=False),
        foreign_keys=[original_image_id],
        lazy=True,
    )
    featured = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (UniqueConstraint("officer_id", "img_id", name="unique_faces"),)


class Image(BaseModel, TrackUpdates):
    __tablename__ = "raw_images"

    id = db.Column(db.Integer, primary_key=True)
    filepath = db.Column(db.String(255), unique=False)
    hash_img = db.Column(db.String(120), unique=False, nullable=True)

    # We might know when the image was taken e.g. through EXIF data
    taken_at = db.Column(
        db.DateTime(timezone=True), index=True, unique=False, nullable=True
    )
    contains_cops = db.Column(db.Boolean, nullable=True)

    is_tagged = db.Column(db.Boolean, default=False, unique=False, nullable=True)

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="raw_images_department_id_fkey"),
    )
    department = db.relationship(
        "Department", backref=db.backref("raw_images", cascade_backrefs=False)
    )


incident_links = db.Table(
    "incident_links",
    db.Column(
        "incident_id",
        db.Integer,
        db.ForeignKey("incidents.id", name="incident_links_incident_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "link_id",
        db.Integer,
        db.ForeignKey("links.id", name="incident_links_link_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "created_at",
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    ),
)

incident_license_plates = db.Table(
    "incident_license_plates",
    db.Column(
        "incident_id",
        db.Integer,
        db.ForeignKey("incidents.id", name="incident_license_plates_incident_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "license_plate_id",
        db.Integer,
        db.ForeignKey(
            "license_plates.id", name="incident_license_plates_license_plate_id_fkey"
        ),
        primary_key=True,
    ),
    db.Column(
        "created_at",
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    ),
)

incident_officers = db.Table(
    "incident_officers",
    db.Column(
        "incident_id",
        db.Integer,
        db.ForeignKey("incidents.id", name="incident_officers_incident_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "officers_id",
        db.Integer,
        db.ForeignKey("officers.id", name="incident_officers_officers_id_fkey"),
        primary_key=True,
    ),
    db.Column(
        "created_at",
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    ),
)


class Location(BaseModel, TrackUpdates):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    street_name = db.Column(db.String(100), index=True)
    cross_street1 = db.Column(db.String(100), unique=False)
    cross_street2 = db.Column(db.String(100), unique=False)
    city = db.Column(db.String(100), unique=False, index=True)
    state = db.Column(db.String(2), unique=False, index=True)
    zip_code = db.Column(db.String(5), unique=False, index=True)

    @validates("zip_code")
    def validate_zip_code(self, key, zip_code):
        if zip_code:
            zip_re = r"^\d{5}$"
            if not re.match(zip_re, zip_code):
                raise ValueError("Not a valid zip code")
            return zip_code

    @validates("state")
    def validate_state(self, key, state):
        return state_validator(state)

    def __repr__(self):
        if self.street_name and self.cross_street1 and self.cross_street2:
            return (
                f"Intersection of {self.street_name} between {self.cross_street1} "
                f"and {self.cross_street2}, {self.city} {self.state}"
            )
        elif self.street_name and self.cross_street2:
            return (
                f"Intersection of {self.street_name} and {self.cross_street2}, "
                + f"{self.city} {self.state}"
            )
        elif self.street_name and self.cross_street1:
            return (
                f"Intersection of {self.street_name} and {self.cross_street1}, "
                + f"{self.city} {self.state}"
            )
        else:
            return f"{self.city} {self.state}"


class LicensePlate(BaseModel, TrackUpdates):
    __tablename__ = "license_plates"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(8), nullable=False, index=True)
    state = db.Column(db.String(2), index=True)

    # for use if car is federal, diplomat, or other non-state
    # non_state_identifier = db.Column(db.String(20), index=True)

    @validates("state")
    def validate_state(self, key, state):
        return state_validator(state)


class Link(BaseModel, TrackUpdates):
    __tablename__ = "links"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), index=True)
    url = db.Column(db.Text(), nullable=False)
    link_type = db.Column(db.String(100), index=True)
    description = db.Column(db.Text(), nullable=True)
    author = db.Column(db.String(255), nullable=True)
    has_content_warning = db.Column(db.Boolean, nullable=False, default=False)

    @validates("url")
    def validate_url(self, key, url):
        return url_validator(url)


class Incident(BaseModel, TrackUpdates):
    __tablename__ = "incidents"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=False, index=True)
    time = db.Column(db.Time, unique=False, index=True)
    report_number = db.Column(db.String(50), index=True)
    description = db.Column(db.Text(), nullable=True)
    address_id = db.Column(
        db.Integer, db.ForeignKey("locations.id", name="incidents_address_id_fkey")
    )
    address = db.relationship(
        "Location",
        backref=db.backref("incidents", cascade_backrefs=False),
        lazy="joined",
    )
    license_plates = db.relationship(
        "LicensePlate",
        secondary=incident_license_plates,
        lazy="subquery",
        backref=db.backref("incidents", cascade_backrefs=False, lazy=True),
    )
    links = db.relationship(
        "Link",
        secondary=incident_links,
        lazy="subquery",
        backref=db.backref("incidents", cascade_backrefs=False, lazy=True),
    )
    officers = db.relationship(
        "Officer",
        secondary=officer_incidents,
        lazy="subquery",
        backref=db.backref(
            "incidents",
            cascade_backrefs=False,
            order_by="Incident.date.desc(), Incident.time.desc()",
        ),
    )
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id", name="incidents_department_id_fkey")
    )
    department = db.relationship(
        "Department", backref=db.backref("incidents", cascade_backrefs=False), lazy=True
    )


class User(UserMixin, BaseModel):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    # A universally unique identifier (UUID) that can be
    # used in place of the user's primary key for things like user
    # lookup queries.
    _uuid = db.Column(
        db.String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    email = db.Column(db.String(64), unique=True, index=True)
    username = db.Column(db.String(64), unique=True, index=True)
    password_hash = db.Column(db.String(128))
    confirmed_at = db.Column(db.DateTime(timezone=True))
    confirmed_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL", name="users_confirmed_by_fkey"),
        unique=False,
    )
    approved_at = db.Column(db.DateTime(timezone=True))
    approved_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL", name="users_approved_by_fkey"),
        unique=False,
    )
    is_area_coordinator = db.Column(db.Boolean, default=False)
    ac_department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id", name="users_ac_department_id_fkey")
    )
    ac_department = db.relationship(
        "Department",
        backref=db.backref("coordinators", cascade_backrefs=False),
        foreign_keys=[ac_department_id],
    )
    is_administrator = db.Column(db.Boolean, default=False)
    disabled_at = db.Column(db.DateTime(timezone=True))
    disabled_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL", name="users_disabled_by_fkey"),
        unique=False,
    )
    last_confirmation_sent_at = db.Column(TZDateTime(timezone=True))
    last_reset_sent_at = db.Column(TZDateTime(timezone=True))

    dept_pref = db.Column(
        db.Integer, db.ForeignKey("departments.id", name="users_dept_pref_fkey")
    )
    dept_pref_rel = db.relationship("Department", foreign_keys=[dept_pref])

    # creator backlinks
    classifications = db.relationship(
        "Image", back_populates=KEY_DB_CREATOR, foreign_keys="Image.created_by"
    )
    descriptions = db.relationship(
        "Description",
        back_populates=KEY_DB_CREATOR,
        foreign_keys="Description.created_by",
    )
    incidents_created = db.relationship(
        "Incident", back_populates=KEY_DB_CREATOR, foreign_keys="Incident.created_by"
    )
    links = db.relationship(
        "Link", back_populates=KEY_DB_CREATOR, foreign_keys="Link.created_by"
    )
    notes = db.relationship(
        "Note", back_populates=KEY_DB_CREATOR, foreign_keys="Note.created_by"
    )
    tags = db.relationship(
        "Face", back_populates=KEY_DB_CREATOR, foreign_keys="Face.created_by"
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=sql_func.now(),
        unique=False,
    )

    __table_args__ = (
        CheckConstraint(
            "(disabled_at IS NULL and disabled_by IS NULL) or (disabled_at IS NOT NULL and disabled_by IS NOT NULL)",
            name="users_disabled_constraint",
        ),
        CheckConstraint(
            "(confirmed_at IS NULL and confirmed_by IS NULL) or (confirmed_at IS NOT NULL and confirmed_by IS NOT NULL)",
            name="users_confirmed_constraint",
        ),
        CheckConstraint(
            "(approved_at IS NULL and approved_by IS NULL) or (approved_at IS NOT NULL and approved_by IS NOT NULL)",
            name="users_approved_constraint",
        ),
    )

    def is_admin_or_coordinator(self, department: Optional[Department]) -> bool:
        return self.is_administrator or (
            department is not None
            and (self.is_area_coordinator and self.ac_department_id == department.id)
        )

    def _jwt_encode(self, payload, expiration):
        secret = current_app.config["SECRET_KEY"]
        header = {"alg": SIGNATURE_ALGORITHM}

        now = int(time.time())
        payload["iat"] = now
        payload["exp"] = now + expiration

        return jwt.encode(header, payload, secret)

    def _jwt_decode(self, token):
        secret = current_app.config["SECRET_KEY"]
        token = jwt.decode(token, secret)
        token.validate()
        return token

    @property
    def password(self):
        raise AttributeError("password is not a readable attribute")

    # mypy has difficulty with mixins, specifically the ones where we define a function
    # twice.
    @password.setter  # type: ignore
    def password(self, password):  # type: ignore
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
        self.regenerate_uuid()

    @property
    def uuid(self):
        return self._uuid

    @staticmethod
    def _case_insensitive_equality(field, value):
        return User.query.filter(func.lower(field) == func.lower(value))

    @staticmethod
    def by_email(email):
        return User._case_insensitive_equality(User.email, email)

    @staticmethod
    def by_username(username):
        return User._case_insensitive_equality(User.username, username)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_confirmation_token(self, expiration=3600):
        payload = {"confirm": self.uuid}
        return self._jwt_encode(payload, expiration).decode(ENCODING_UTF_8)

    def confirm(self, token, confirming_user_id: int):
        try:
            data = self._jwt_decode(token)
        except JoseError as e:
            current_app.logger.warning("failed to decrypt token: %s", e)
            return False
        if data.get("confirm") != self.uuid:
            current_app.logger.warning(
                "incorrect uuid here, expected %s, got %s",
                data.get("confirm"),
                self.uuid,
            )
            return False
        self.confirmed_at = datetime.now(timezone.utc)
        self.confirmed_by = confirming_user_id
        db.session.add(self)
        db.session.commit()
        return True

    def generate_reset_token(self, expiration=3600):
        payload = {"reset": self.uuid}
        return self._jwt_encode(payload, expiration).decode(ENCODING_UTF_8)

    def reset_password(self, token, new_password):
        try:
            data = self._jwt_decode(token)
        except JoseError:
            return False
        if data.get("reset") != self.uuid:
            return False
        self.password = new_password
        db.session.add(self)
        db.session.commit()
        return True

    def generate_email_change_token(self, new_email, expiration=3600):
        payload = {"change_email": self.uuid, "new_email": new_email}
        return self._jwt_encode(payload, expiration).decode(ENCODING_UTF_8)

    def change_email(self, token):
        try:
            data = self._jwt_decode(token)
        except JoseError:
            return False
        if data.get("change_email") != self.uuid:
            return False
        new_email = data.get("new_email")
        if new_email is None:
            return False
        if self.query.filter_by(email=new_email).first() is not None:
            return False
        self.email = new_email
        self.regenerate_uuid()
        db.session.add(self)
        db.session.commit()
        return True

    def regenerate_uuid(self):
        self._uuid = str(uuid.uuid4())

    def get_id(self):
        """Get the Flask-Login user identifier, NOT THE DATABASE ID."""
        return str(self.uuid)

    @property
    def is_active(self):
        """Override UserMixin.is_active to prevent disabled users from logging in."""
        return not self.disabled_at

    def approve_user(self, approving_user_id: int):
        """Handle approving logic."""
        if self.approved_at or self.approved_by:
            return False

        self.approved_at = datetime.now(timezone.utc)
        self.approved_by = approving_user_id
        db.session.add(self)
        db.session.commit()
        return True

    def confirm_user(self, confirming_user_id: int):
        """Handle confirming logic."""
        if self.confirmed_at or self.confirmed_by:
            return False

        self.confirmed_at = datetime.now(timezone.utc)
        self.confirmed_by = confirming_user_id
        db.session.add(self)
        db.session.commit()
        return True

    def disable_user(self, disabling_user_id: int):
        """Handle disabling logic."""
        if self.disabled_at or self.disabled_by:
            return False

        self.disabled_at = datetime.now(timezone.utc)
        self.disabled_by = disabling_user_id
        db.session.add(self)
        db.session.commit()
        return True
