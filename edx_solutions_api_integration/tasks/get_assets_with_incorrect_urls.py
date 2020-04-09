import logging
import pymongo
import re
import datetime
from urlparse import urlparse, urljoin
from pytz import UTC
import csv
import StringIO

from bson.son import SON
from celery.task import task, Task

from django.db.models import Q
from django.core.mail import EmailMessage
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

        if update_script:
            headers = ['Asset', 'Course', 'Module', 'Updated']
        else:
            headers = ['Asset', 'Course', 'Module', 'Available']
        rows = []
        for course, assets in result.items():
            for asset in assets:
                rows.append([
                    asset.get('name'), course, asset.get('module'),
                    asset.get('available') == True
                ])

        assets_report = StringIO.StringIO()
        writer = csv.writer(assets_report)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)

        subject = '{} assets with incorrect urls task completed'.format('Update' if update_script else 'Get')
        email = EmailMessage(subject, '', settings.DEFAULT_FROM_EMAIL, email_ids)
        email.attach('assets_report.csv', assets_report.getvalue(), 'text/csv')
        email.send(fail_silently=False)


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
def get_assets_with_incorrect_urls(
        self, course_ids, course_type, email_ids,
        environment, studio_url, staff_user_id, update
    ):
    task_id = self.request.id
    if not course_ids:
        if course_type == 'close':
            course_ids = CourseOverview.objects.filter(
                Q(end__lte=datetime.datetime.today().replace(tzinfo=UTC))
            ).values_list('id', flat=True)
        else:
            course_ids = CourseOverview.objects.filter(
                Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
                Q(end__isnull=True)
            ).values_list('id', flat=True)

    courses_assets = dict()

    for course_id in course_ids:
        course_assets = find_asset_urls_in_course(task_id, course_id, environment, studio_url, staff_user_id, update)
        if course_assets:
            courses_assets[course_id] = course_assets

    if update:
        update_courses_cache(course_ids)

    return courses_assets


def find_asset_urls_in_course(task_id, course_id, environment, studio_url, staff_user_id, update):
    block_url = '{domain}/container/i4x://{org}/{course}/{category}/{name}'
    course_key = CourseKey.from_string(course_id)
    query = SON([
        ('_id.tag', 'i4x'),
        ('_id.org', course_key.org),
        ('_id.course', course_key.course),
    ])

    _store = store._get_modulestore_for_courselike(course_key)

    # split_mongo based courses are not supported
    if not hasattr(_store, 'collection'):
        return []

    blocks = list(_store.collection.find(
        query,
        sort=[('_id.revision', pymongo.DESCENDING)],
    ))

    course_assets = []
    blocks_to_update = []

    for block in blocks:
        block_loc = block_url.format(
            domain=studio_url,
            org=block.get('_id', {}).get('org'),
            course=block.get('_id', {}).get('course'),
            category=block.get('_id', {}).get('category'),
            name=block.get('_id', {}).get('name')
        )
        block_assets = list()
        _find_asset_urls_in_block(
            task_id, block, block_loc, block_assets, course_key,
            environment, staff_user_id, update
        )

        if block_assets:
            course_assets.extend(block_assets)
            blocks_to_update.append(block)

    if update:
        if blocks_to_update:
            for module in _store._load_items(course_key, blocks_to_update):
                store.update_item(xblock=module, user_id=staff_user_id)

    return course_assets


def _find_asset_urls_in_block(
        task_id, value, block_loc,
        block_assets, course_key,
        environment, staff_user_id,
        update,
        dictionary=None,
        value_key=None,
    ):

    if type(value) == dict:
        for key, val in value.items():
            _find_asset_urls_in_block(
                task_id, val, block_loc, block_assets,
                course_key, environment, staff_user_id, update,
                dictionary=value, value_key=key
            )
    elif type(value) == list:
        for item in value:
            _find_asset_urls_in_block(
                task_id, item, block_loc, block_assets,
                course_key, environment, staff_user_id, update,
                dictionary=dictionary, value_key=value_key
            )
    elif type(value) in (str, unicode):
        save_updated = False
        urls = re.findall(URL_RE, value)

        for url in urls:
            parsed_url = urlparse(url)
            asset_url = StaticContent.ASSET_URL_RE.match(parsed_url.path)

            if asset_url is not None:
                # check if asset URL belongs to some other server or course
                if parsed_url.hostname != environment or \
                                asset_url.groupdict().get('course') != course_key.course:

                    asset_info = {'name':  asset_url.groupdict().get('name'), 'module':block_loc, 'available': False}
                    asset_path = '{}{}'.format(
                        StaticContent.get_base_url_path_for_course_assets(course_key),
                        asset_url.groupdict().get('name')
                    )

                    # check if asset exists in this course
                    try:
                        loc = StaticContent.get_location_from_path(asset_path)
                    except (InvalidLocationError, InvalidKeyError):
                        pass
                    else:
                        try:
                            AssetManager.find(loc, as_stream=True)
                        except (ItemNotFoundError, NotFoundError):
                            pass
                        else:
                            asset_info['available'] = True

                            if update:
                                # replace url with the `asset_path`
                                full_asset_path = urljoin('https://{}'.format(environment), asset_path)
                                value = value.replace(url, full_asset_path, 1)
                                save_updated = True
                                log.info('[{}] Replacing `{}` with new path `{}` in module `{}`'
                                            .format(task_id, url, full_asset_path, block_loc))

                    block_assets.append(asset_info)

        if urls and save_updated and update:
            dictionary[value_key] = value
