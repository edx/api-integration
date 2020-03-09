import logging
import pymongo
import re
import datetime
from urlparse import urlparse, urljoin
from pytz import UTC

from bson.son import SON
from celery.task import task, Task

from django.db.models import Q
from django.core.mail import send_mail
from django.conf import settings

from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore
from xmodule.contentstore.content import StaticContent
from xmodule.modulestore import InvalidLocationError
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.exceptions import NotFoundError
from xmodule.assetstore.assetmgr import AssetManager
from opaque_keys import InvalidKeyError
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.block_structure.api import update_course_in_cache

log = logging.getLogger(__name__)
store = modulestore()

URL_RE = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'


class AssetURLsTask(Task):
    def on_success(self, result, task_id, args, kwargs):
        email_ids = kwargs.get('email_ids')
        update_script = kwargs.get('update')

        results = ''
        if result:
            for course, blocks in result.items():
                if blocks:
                    results += '''\n\n {}:'''.format(course)
                    results += '''\n\n{}'''.format('\n'.join(blocks))

        subject = '{} assets with incorrect urls task completed'.format('Update' if update_script else 'Get')
        if update_script:
            text = '''Following modules could not be updated because one or more
            assets could not be found in the course:{}'''.format(results)
        else:
            text = '''Following is the list of modules where incorrect asset links exist:{}'''.format(results)

        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, email_ids)


def update_courses_cache(course_ids):
    """
    Updates course cache so API returns updated data
    """
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        try:
            update_course_in_cache(course_key)
        except:
            continue


@task(
    name=u'lms.djangoapps.api_integration.tasks.get_assets_with_incorrect_urls',
    bind=True,
    base=AssetURLsTask
)
def get_assets_with_incorrect_urls(self, course_ids, email_ids, environment, staff_user_id, update):
    task_id = self.request.id
    if not course_ids:
        course_ids = CourseOverview.objects.filter(
            Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
            Q(end__isnull=True)
        ).values_list('id', flat=True)

    course_asset_blocks = dict()
    for course_id in course_ids:
        asset_blocks = find_asset_urls_in_course(task_id, course_id, environment, staff_user_id, update)
        if asset_blocks:
            course_asset_blocks[course_id] = asset_blocks

    if update:
        update_courses_cache(course_ids)

    return course_asset_blocks


def find_asset_urls_in_course(task_id, course_id, environment, staff_user_id, update):
    block_url = '/courses/{}/lessons/jump_to_page/{}'
    course_key = CourseKey.from_string(course_id)
    query = SON([
        ('_id.tag', 'i4x'),
        ('_id.org', course_key.org),
        ('_id.course', course_key.course),
    ])

    _store = store._get_modulestore_for_courselike(course_key)
    blocks = list(_store.collection.find(
        query,
        sort=[('_id.revision', pymongo.DESCENDING)],
    ))

    asset_blocks = []
    failure_blocks = []
    for block in blocks:
        asset_urls = dict(success=[], failure=[])
        block_loc = block_url.format(course_key, block.get('_id', {}).get('name'))
        _find_asset_urls_in_block(task_id, block, block_loc, asset_urls, course_key, environment, staff_user_id, update)
        if update:
            if asset_urls['success']:
                asset_blocks.append(block)
            if asset_urls['failure']:
                loc = block.get('_id', {}).get('name')
                failure_blocks.append(block_url.format(course_id, loc))
        elif asset_urls['success']:
            loc = block.get('_id', {}).get('name')
            asset_blocks.append(block_url.format(course_id, loc))

    if update:
        if asset_blocks:
            for module in _store._load_items(course_key, asset_blocks):
                store.update_item(xblock=module, user_id=staff_user_id)
        if failure_blocks:
            return failure_blocks
        else:
            return []

    return asset_blocks


def _find_asset_urls_in_block(task_id, block, block_loc, asset_urls, course_key, environment, staff_user_id, update):
    for key, value in block.items():
        if type(value) == dict:
            _find_asset_urls_in_block(
                task_id, value, block_loc, asset_urls,
                course_key, environment, staff_user_id,
                update
            )
        if type(value) == str or type(value) == unicode:
            urls = re.findall(URL_RE, value)
            for url in urls:
                parsed_url = urlparse(url)
                asset_url = StaticContent.ASSET_URL_RE.match(parsed_url.path)

                if asset_url is not None:
                    # check if asset URL belongs to some other server or course
                    if parsed_url.hostname != environment or \
                                    asset_url.groupdict().get('course') != course_key.course:

                        if update:
                            # check if asset exists in this course
                            asset_path = '{}{}'.format(
                                StaticContent.get_base_url_path_for_course_assets(course_key),
                                asset_url.groupdict().get('name')
                            )

                            try:
                                loc = StaticContent.get_location_from_path(asset_path)
                            except (InvalidLocationError, InvalidKeyError):
                                asset_urls['failure'].append(block)
                                log.warning(
                                    '[{}] Could not find asset `{}` in module `{}`. Asset `{}` could not be updated.'
                                        .format(task_id, asset_path, block_loc, url)
                                )
                            else:
                                try:
                                    AssetManager.find(loc, as_stream=True)
                                except (ItemNotFoundError, NotFoundError):
                                    asset_urls['failure'].append(block)
                                    log.warning(
                                        '[{}] Could not find asset `{}` in module `{}`. Asset `{}` could not be updated.'
                                            .format(task_id, asset_path, block_loc, url)
                                    )
                                else:
                                    # replace url with the `asset_path`
                                    full_asset_path = urljoin('https://{}'.format(environment), asset_path)
                                    block[key] = value.replace(url, full_asset_path)
                                    asset_urls['success'].append(block)

                                    log.info('[{}] Replacing `{}` with new path `{}` in module `{}`'
                                                .format(task_id, url, full_asset_path, block_loc))
                        else:
                            asset_urls['success'].append(url)
