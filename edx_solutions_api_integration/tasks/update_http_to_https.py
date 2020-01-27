import logging
import pymongo
import re
from bson.son import SON
from celery.task import task
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore

from openedx.core.djangoapps.content.block_structure.api import update_course_in_cache

log = logging.getLogger(__name__)
store = modulestore()


@task(name=u'lms.djangoapps.api_integration.tasks.update_http_to_https')
def update_http_to_https(course_ids, staff_user_id):
    update_urls_in_courses(course_ids, staff_user_id)
    update_courses_cache(course_ids)


def update_urls_in_courses(course_ids, staff_user_id):
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        query = SON([
            ('_id.tag', 'i4x'),
            ('_id.org', course_key.org),
            ('_id.course', course_key.course),
        ])

        _store = store._get_modulestore_for_courselike(course_key)
        items = list(_store.collection.find(
            query,
            sort=[('_id.revision', pymongo.DESCENDING)],
        ))
        updated_blocks = []
        for item in items:
            updated, block = update_content(course_id, item)
            if updated:
                updated_blocks += [block]

        update_blocks(course_key, updated_blocks, staff_user_id, _store)


def update_content(course_id, block):
    updated = False
    for key, value in block.items():
        if type(value) == dict:
            is_updated, updated_block = update_content(course_id, value)
            updated = updated | is_updated
            block[key] = updated_block

        if type(value) == str or type(value) == unicode and 'http://' in value:
            matches = re.findall('(http://[^\s]*)', value)
            log.info('Updating urls %s for course %s' % (' '.join(matches), course_id))
            block[key] = value.replace('http://', 'https://')
            updated = True

    return updated, block


def update_blocks(course_key, updated_blocks, staff_user_id, _store):
    if not updated_blocks:
        return

    for module in _store._load_items(course_key, updated_blocks):
        store.update_item(xblock=module, user_id=staff_user_id)


def update_courses_cache(course_ids):
    """
    Updates course cache so API returns updated data
    """
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        update_course_in_cache(course_key)
