# Routing and view tests
import os
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from flask import current_app, url_for

from OpenOversight.app.main import views
from OpenOversight.app.main.forms import FaceTag
from OpenOversight.app.models.database import (
    Assignment,
    Department,
    Face,
    Image,
    Job,
    Officer,
    User,
)
from OpenOversight.app.utils.constants import ENCODING_UTF_8
from OpenOversight.tests.conftest import AC_DEPT
from OpenOversight.tests.constants import INVALID_ID
from OpenOversight.tests.routes.route_helpers import login_ac, login_admin, login_user


PROJECT_ROOT = os.path.abspath(os.curdir)


@pytest.mark.parametrize(
    "route",
    [
        "/labels",
        "/tutorial",
        "/tags/1",
    ],
)
def test_routes_ok(route, client, mockdata):
    rv = client.get(route)
    assert rv.status_code == HTTPStatus.OK


# All login_required views should redirect if there is no user logged in
@pytest.mark.parametrize(
    "route",
    [
        "/leaderboard",
        "/sort/departments/1",
        "/cop_faces/departments/1",
        "/images/1",
        "/images/tagged/1",
    ],
)
def test_route_login_required(route, client, mockdata):
    rv = client.get(route)
    assert rv.status_code == HTTPStatus.FOUND


def test_invalid_department_image_sorting(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(url_for("main.sort_images", department_id=INVALID_ID))
        assert rv.status_code == HTTPStatus.NOT_FOUND


# POST-only routes
@pytest.mark.parametrize(
    "route",
    [
        "/officers/3/assignments/new",
        "/tags/delete/1",
        "/tags/set_featured/1",
        "/images/classify/1/1",
    ],
)
def test_route_post_only(route, client):
    rv = client.get(route)
    assert rv.status_code == HTTPStatus.METHOD_NOT_ALLOWED


def test_logged_in_user_can_access_sort_form(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(
            url_for("main.sort_images", department_id=1), follow_redirects=True
        )
        assert b"Do you see uniformed law enforcement officers in the photo" in rv.data


def test_invalid_officer_id_display_submission(client, session):
    with current_app.test_request_context():
        login_admin(client)

        rv = client.get(
            url_for("main.display_submission", image_id=INVALID_ID),
        )
        assert rv.status_code == HTTPStatus.NOT_FOUND


def test_user_can_view_submission(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(
            url_for("main.display_submission", image_id=1), follow_redirects=True
        )
        assert b"Image ID" in rv.data


def test_invalid_tag_id_display_tag(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(url_for("main.display_tag", tag_id=INVALID_ID))
        assert rv.status_code == HTTPStatus.NOT_FOUND


def test_user_can_view_tag(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(url_for("main.display_tag", tag_id=1), follow_redirects=True)
        assert b"Tag" in rv.data

        # Check that tag frame position is specified
        for attribute in (b"data-top", b"data-left", b"data-width", b"data-height"):
            assert attribute in rv.data


def test_invalid_id_delete_tag(client, session):
    with current_app.test_request_context():
        login_admin(client)

        rv = client.post(url_for("main.delete_tag", tag_id=INVALID_ID))
        assert rv.status_code == HTTPStatus.NOT_FOUND


def test_admin_can_delete_tag(client, session):
    with current_app.test_request_context():
        login_admin(client)

        rv = client.post(url_for("main.delete_tag", tag_id=1), follow_redirects=True)
        assert b"Deleted this tag" in rv.data


def test_ac_can_delete_tag_in_their_dept(client, session):
    with current_app.test_request_context():
        login_ac(client)

        tag = Face.query.filter(Face.officer.has(department_id=AC_DEPT)).first()
        tag_id = tag.id

        rv = client.post(
            url_for("main.delete_tag", tag_id=tag_id), follow_redirects=True
        )
        assert b"Deleted this tag" in rv.data

        # test tag was deleted from database
        deleted_tag = session.get(Face, tag_id)
        assert deleted_tag is None


def test_ac_cannot_delete_tag_not_in_their_dept(client, session):
    with current_app.test_request_context():
        login_ac(client)

        tag = (
            Face.query.join(Face.officer)
            .except_(Face.query.filter(Face.officer.has(department_id=AC_DEPT)))
            .first()
        )

        tag_id = tag.id

        rv = client.post(
            url_for("main.delete_tag", tag_id=tag_id), follow_redirects=True
        )
        assert rv.status_code == HTTPStatus.FORBIDDEN

        # test tag was not deleted from database
        deleted_tag = session.get(Face, tag_id)
        assert deleted_tag is not None


@patch(
    "OpenOversight.app.utils.general.serve_image",
    MagicMock(return_value=PROJECT_ROOT + "/app/static/images/test_cop1.png"),
)
def test_user_can_add_tag(client, session):
    with current_app.test_request_context():
        mock = MagicMock(return_value=Image.query.first())
        with patch("OpenOversight.app.main.views.crop_image", mock):
            officer = Officer.query.filter_by(department_id=1).first()
            image = Image.query.filter_by(department_id=1).first()
            login_user(client)
            user = User.query.filter_by(is_administrator=True).first()

            form = FaceTag(
                department_id=officer.department_id,
                star_no=officer.assignments[0].star_no,
                image_id=image.id,
                dataX=34,
                dataY=32,
                dataWidth=3,
                dataHeight=33,
                created_by=user.id,
            )
            rv = client.post(
                url_for("main.label_data", image_id=image.id),
                data=form.data,
                follow_redirects=True,
            )
            views.crop_image.assert_called_once()
            assert b"Tag added to database" in rv.data


def test_user_must_disambiguate_serial_number_with_multiple_officers(
    mockdata, client, session
):
    with current_app.test_request_context():
        login_user(client)

        department = Department.query.first()
        star_no = "1312"

        # Create 2 officers with the same star_no
        job = Job.query.first()
        assignment1 = Assignment(star_no=star_no, job_id=job.id)
        assignment2 = Assignment(star_no=star_no, job_id=job.id)
        officer1 = Officer(department_id=department.id, assignments=[assignment1])
        officer2 = Officer(department_id=department.id, assignments=[assignment2])
        session.add(assignment1)
        session.add(assignment2)
        session.add(officer1)
        session.add(officer2)

        image = Image.query.first()

        # Ensure disambiguation page is shown
        form = FaceTag(
            department_id=department.id,
            star_no=star_no,
            image_id=image.id,
            dataX=34,
            dataY=32,
            dataWidth=3,
            dataHeight=33,
        )
        rv = client.post(
            url_for("main.label_data", image_id=image.id),
            data=form.data,
            follow_redirects=True,
        )
        assert b"Multiple officers found" in rv.data

        # Resolve conflict by providing officer_id
        officer = (
            Officer.query.join(Assignment, Officer.id == Assignment.officer_id)
            .filter(Assignment.star_no == star_no)
            .first()
        )
        form.officer_id.data = officer.id
        rv = client.post(
            url_for("main.label_data", image_id=image.id),
            data=form.data,
            follow_redirects=True,
        )
        assert b"Tag added to database" in rv.data


def test_user_cannot_open_image_in_tagger_if_has_already_been_tagged(
    mockdata, client, session
):
    with current_app.test_request_context():
        login_user(client)

        session.add(Image(is_tagged=True))
        image = Image.query.filter_by(is_tagged=True).first()

        rv = client.get(
            url_for("main.label_data", image_id=image.id),
            follow_redirects=False,
        )
        assert rv.status_code == 302


def test_user_cannot_add_tag_if_it_exists(client, session):
    with current_app.test_request_context():
        _, user = login_user(client)

        tag = Face.query.first()
        form = FaceTag(
            department_id=tag.officer.department_id,
            star_no=tag.officer.assignments[0].star_no,
            image_id=tag.original_image_id,
            dataX=34,
            dataY=32,
            dataWidth=3,
            dataHeight=33,
            created_by=user.id,
        )

        rv = client.post(
            url_for("main.label_data", image_id=tag.original_image_id),
            data=form.data,
            follow_redirects=True,
        )
        assert (
            b"Tag already exists between this officer and image! Tag not added."
            in rv.data
        )


def test_user_cannot_tag_nonexistent_officer(client, session):
    with current_app.test_request_context():
        _, user = login_user(client)

        tag = Face.query.first()
        form = FaceTag(
            department_id=Department.query.first().id,
            star_no=999999999999999999,
            image_id=tag.img_id,
            dataX=34,
            dataY=32,
            dataWidth=3,
            dataHeight=33,
            created_by=user.id,
        )

        rv = client.post(
            url_for("main.label_data", image_id=tag.img_id),
            data=form.data,
            follow_redirects=True,
        )
        assert b"Are you sure that is the correct officer serial number?" in rv.data


def test_user_cannot_tag_officer_mismatched_with_department(client, session):
    with current_app.test_request_context():
        _, user = login_user(client)
        tag = Face.query.first()

        form = FaceTag(
            department_id=tag.officer.department_id,
            star_no=tag.officer.assignments[0].star_no,
            officer_id=tag.officer_id,
            image_id=tag.original_image_id,
            dataX=34,
            dataY=32,
            dataWidth=3,
            dataHeight=33,
            created_by=user.id,
        )

        rv = client.post(
            url_for("main.label_data", department_id=2, image_id=tag.original_image_id),
            data=form.data,
            follow_redirects=True,
        )

        department = session.get(Department, 2)
        assert (f"The officer is not in {department.name}, {department.state}.").encode(
            ENCODING_UTF_8
        ) in rv.data


def test_invalid_id_complete_tagging(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(url_for("main.complete_tagging", image_id=INVALID_ID))
        assert rv.status_code == HTTPStatus.NOT_FOUND


def test_complete_tagging(client, session):
    with current_app.test_request_context():
        _, user = login_user(client)
        image_id = 4

        rv = client.get(
            url_for("main.complete_tagging", image_id=image_id), follow_redirects=True
        )
        image = session.get(Image, image_id)

        assert b"Marked image as completed." in rv.data
        assert image.last_updated_by == user.id


def test_user_can_view_leaderboard(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.get(url_for("main.leaderboard"), follow_redirects=True)
        assert b"Top Users by Number of Images Sorted" in rv.data


def test_user_is_redirected_to_correct_department_after_tagging(client, session):
    with current_app.test_request_context():
        login_user(client)
        department_id = 2
        image = Image.query.filter_by(department_id=department_id, faces=None).first()
        rv = client.get(
            url_for(
                "main.complete_tagging", image_id=image.id, department_id=department_id
            ),
            follow_redirects=True,
        )
        department = session.get(Department, department_id)

        assert rv.status_code == HTTPStatus.OK
        assert "Marked image as completed" in rv.data.decode(ENCODING_UTF_8)
        assert department.name in rv.data.decode(ENCODING_UTF_8)


def test_invalid_id_set_featured_tag(client, session):
    with current_app.test_request_context():
        login_admin(client)

        rv = client.post(
            url_for("main.set_featured_tag", tag_id=INVALID_ID), follow_redirects=True
        )
        assert rv.status_code == HTTPStatus.NOT_FOUND


def test_user_can_set_featured_tag(client, session):
    with current_app.test_request_context():
        login_user(client)

        rv = client.post(
            url_for("main.set_featured_tag", tag_id=1), follow_redirects=True
        )
        assert b"Successfully set this tag as featured" in rv.data


def test_can_set_featured_tag_login_required(client, session):
    rv = client.post("/tag/set_featured/1", follow_redirects=False)
    assert rv.status_code == HTTPStatus.FOUND


@patch(
    "OpenOversight.app.utils.general.serve_image",
    MagicMock(return_value=PROJECT_ROOT + "/app/static/images/test_cop1.png"),
)
def test_featured_tag_replaces_others(client, session):
    with current_app.test_request_context():
        _, user = login_admin(client)

        tag1 = Face.query.first()
        officer = session.get(Officer, tag1.officer_id)

        # Add second tag for officer
        second_image = (
            Image.query.filter(Image.department_id == officer.department_id)
            .filter(Image.id != tag1.img_id)
            .first()
        )
        assert second_image is not None
        mock = MagicMock(return_value=second_image)
        with patch("OpenOversight.app.main.views.crop_image", mock):
            form = FaceTag(
                department_id=officer.department_id,
                star_no=officer.assignments[0].star_no,
                image_id=second_image.id,
                dataX=34,
                dataY=32,
                dataWidth=3,
                dataHeight=33,
                created_by=user.id,
            )
            rv = client.post(
                url_for("main.label_data", image_id=second_image.id),
                data=form.data,
                follow_redirects=True,
            )
            views.crop_image.assert_called_once()
            assert b"Tag added to database" in rv.data

        tag2 = (
            Face.query.filter(Face.officer_id == tag1.officer_id)
            .filter(Face.id != tag1.id)
            .one_or_none()
        )
        assert tag2 is not None

        # Set tag 1 as featured
        rv = client.post(
            url_for("main.set_featured_tag", tag_id=tag1.id), follow_redirects=True
        )
        assert b"Successfully set this tag as featured" in rv.data

        tag1 = Face.query.filter(Face.id == tag1.id).one()
        assert tag1.featured is True

        # Set tag 2 as featured
        rv = client.post(
            url_for("main.set_featured_tag", tag_id=tag2.id), follow_redirects=True
        )
        assert b"Successfully set this tag as featured" in rv.data

        tag1 = Face.query.filter(Face.id == tag1.id).one()
        tag2 = Face.query.filter(Face.id == tag2.id).one()
        assert tag1.featured is False
        assert tag2.featured is True
