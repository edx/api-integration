""" Centralized access to LMS courseware app """
from django.contrib.auth.models import AnonymousUser

from courseware import courses, module_render
from courseware.model_data import FieldDataCache
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locations import SlashSeparatedCourseKey, Location
from xmodule.modulestore import InvalidLocationError
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError


def _anonymous_known_flag(user):
    """
    Returns know flag for anonymous user
    """
    if isinstance(user, AnonymousUser):
        user.known = False


def get_modulestore():
    """
    Returns modulestore
    """
    return modulestore()


def get_course(request, user, course_id, depth=0, load_content=False):
    """
    Utility method to obtain course components
    """
    _anonymous_known_flag(user)
    course_descriptor = None
    course_content = None
    course_key = get_course_key(course_id)
    if course_key:
        course_descriptor = get_course_descriptor(course_key, depth)
        if course_descriptor and load_content:
            course_content = get_course_content(request, user, course_key, course_descriptor)
    return course_descriptor, course_key, course_content


def get_course_child(request, user, course_key, content_id, load_content=False):
    """
    Return a course xmodule/xblock to the caller
    """
    _anonymous_known_flag(user)
    child_descriptor = None
    child_content = None
    child_key = get_course_child_key(content_id)
    if child_key:
        child_descriptor = get_course_child_descriptor(child_key)
        if child_descriptor and load_content:
            child_content = get_course_child_content(request, user, course_key, child_descriptor)
    return child_descriptor, child_key, child_content


def get_course_total_score(course_summary):
    """
    Traverse course summary to calculate max possible score for a course
    """
    score = 0
    for chapter in course_summary:  # accumulate score of each chapter
        for section in chapter['sections']:
            if section['section_total']:
                score += section['section_total'][1]
    return score


def get_course_key(course_id, slashseparated=False):
    """
    Returns course_key object generated from course_id
    """
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError:
        try:
            course_key = SlashSeparatedCourseKey.from_deprecated_string(course_id)
        except InvalidKeyError:
            course_key = None
    if slashseparated:
        try:
            course_key = course_key.to_deprecated_string()
        except:  # pylint: disable=W0702
            course_key = course_id
    return course_key


def get_course_descriptor(course_key, depth):
    """
    Returns course descriptor
    """
    try:
        course_descriptor = courses.get_course(course_key, depth)
    except ValueError:
        course_descriptor = None
    return course_descriptor


def get_course_content(request, user, course_key, course_descriptor):  # pylint: disable=W0613
    """
    Returns course content
    """
    field_data_cache = FieldDataCache([course_descriptor], course_key, user)
    course_content = module_render.get_module_for_descriptor(
        user,
        request,
        course_descriptor,
        field_data_cache,
        course_key)
    return course_content


def course_exists(course_id):
    """Checks if course exists."""
    course_key = get_course_key(course_id)
    return course_key and get_modulestore().has_course(course_key)


def get_course_child_key(content_id):
    """
    Returns course child key
    """
    try:
        content_id = UsageKey.from_string(content_id)
    except InvalidKeyError:
        try:
            content_id = Location.from_deprecated_string(content_id)
        except (InvalidLocationError, InvalidKeyError):
            content_id = None
    return content_id


def get_course_child_descriptor(child_key):
    """
    Returns course child descriptor
    """
    try:
        content_descriptor = get_modulestore().get_item(child_key)
    except ItemNotFoundError:
        content_descriptor = None
    return content_descriptor


def get_course_child_content(request, user, course_key, child_descriptor):
    """
    Returns course child content
    """
    field_data_cache = FieldDataCache([child_descriptor], course_key, user)
    child_content = module_render.get_module_for_descriptor(
        user,
        request,
        child_descriptor,
        field_data_cache,
        course_key)
    return child_content
