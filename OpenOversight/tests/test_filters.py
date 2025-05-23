from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import us.states
from flask import current_app, session

from OpenOversight.app import filters
from OpenOversight.app.utils.constants import FIELD_NOT_AVAILABLE, KEY_TIMEZONE


@pytest.mark.parametrize(
    "user_tz_str, expected_tz",
    [
        ("America/New_York", ZoneInfo("America/New_York")),
        # Default timezone is set to CST
        ("Invalid timezone", ZoneInfo("America/Chicago")),
    ],
)
def test_get_timezone(app, user_tz_str, expected_tz):
    with current_app.test_request_context():
        session[KEY_TIMEZONE] = user_tz_str
        actual_tz = filters.get_timezone()
        assert expected_tz == actual_tz


def test_capfirst():
    assert "Some words" == filters.capfirst_filter("some words")


def test_get_age_from_birth_year(app):
    with current_app.test_request_context():
        now = datetime(2020, 1, 1)
        with patch("OpenOversight.app.filters.datetime") as mock_dt:
            mock_dt.now.return_value = now
            assert 100 == filters.get_age_from_birth_year(1920)


@pytest.mark.parametrize(
    "data, is_in",
    [
        ({}, False),
        ({"name": "test"}, True),
    ],
)
def test_field_in_query(data, is_in):
    expected = " show " if is_in else ""
    assert expected == filters.field_in_query(data, "name")


@pytest.mark.parametrize(
    "value, expected_output",
    [(None, FIELD_NOT_AVAILABLE), (datetime(2020, 1, 1), "Jan 01, 2020")],
)
def test_display_date(value, expected_output):
    assert expected_output == filters.display_date(value)


@pytest.mark.parametrize(
    "value, expected_output",
    [
        (None, FIELD_NOT_AVAILABLE),
        # In CST timezone, this would still be 12/31/2019
        (datetime(2020, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")), "Dec 31, 2019"),
    ],
)
def test_local_date(value, expected_output):
    with current_app.test_request_context():
        assert expected_output == filters.local_date(value)


@pytest.mark.parametrize(
    "value, expected_output",
    [
        (None, FIELD_NOT_AVAILABLE),
        (
            datetime(2020, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
            "06:00 PM (CST) on Dec 31, 2019",
        ),
    ],
)
def test_local_date_time(value, expected_output):
    with current_app.test_request_context():
        assert expected_output == filters.local_date_time(value)


@pytest.mark.parametrize(
    "value, expected_output",
    [
        (None, FIELD_NOT_AVAILABLE),
        (datetime(2020, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")), "12:00 AM"),
    ],
)
def test_display_time(value, expected_output):
    with current_app.test_request_context():
        assert expected_output == filters.display_time(value)


@pytest.mark.parametrize(
    "value, expected_output",
    [
        (None, FIELD_NOT_AVAILABLE),
        (datetime(2020, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")), "06:00 PM (CST)"),
    ],
)
def test_local_time(value, expected_output):
    with current_app.test_request_context():
        assert expected_output == filters.local_time(value)


def test_thousands_separator():
    assert "1,234,567" == filters.thousands_separator(1234567)


def test_get_state_full_name():
    assert "N/A" == filters.get_state_full_name("??")
    assert "Federal" == filters.get_state_full_name("FA")
    with patch("us.states.lookup") as mock_lookup:
        mock_lookup.return_value = us.states.OR
        assert "Oregon" == filters.get_state_full_name("test")
        mock_lookup.assert_called_with("test")
