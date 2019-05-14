import logging
import urllib

from lxml import etree
from PIL import Image
from urlparse import urljoin
from celery.task import task

from django.conf import settings

from xmodule.modulestore.django import modulestore
from opaque_keys.edx.keys import CourseKey


logger = logging.getLogger('edx.celery.task')
store = modulestore()


@task(name=u'lms.djangoapps.api_integration.tasks.update_image_explorer_schema')
def update_image_explorer_schema(staff_user_id, course_ids):
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        ie_blocks = store.get_items(
            course_key,
            qualifiers={"category": 'image-explorer'}
        )

        for block in ie_blocks:
            try:
                etree.fromstring(block.data)
            except:
                logger.error('Invalid Image Explorer XML data for block `{}` in Course: {}. Skipping'
                             .format(block.parent.block_id, course_id))
            else:
                _upgrade_xml_schema(block, course_id, staff_user_id)


def _upgrade_xml_schema(block, course_id, staff_user_id):
    xmltree = etree.fromstring(block.data)
    schema_version = int(xmltree.attrib.get('schema_version', 1))

    if schema_version > 1:
        return

    logger.info('Updating IE schema for block `{}` in course `{}`'.format(block.parent.block_id, course_id))
    xmltree.set('schema_version', '2')
    hotspots_element = xmltree.find('hotspots')
    hotspot_elements = hotspots_element.findall('hotspot')

    for index, hotspot_element in enumerate(hotspot_elements):
        if not hotspot_element.get('x').endswith('%') or not hotspot_element.get('y').endswith('%'):
            _convert_to_percentage_coordinates(xmltree, hotspot_element, course_id)

    block.data = etree.tostring(xmltree)
    store.update_item(xblock=block, user_id=staff_user_id)
    logger.info('Successfully Updated IE schema for block `{}` in course: `{}`'
                .format(block.parent.block_id, course_id))


def _convert_to_percentage_coordinates(xmltree, hotspot, course_id):
    background = xmltree.find('background')
    width = background.get('width')
    height = background.get('height')

    if None in (width, height):
        image_url = _replace_static_from_url(background.get('src'), course_id=course_id)
        img_size = _get_image_dimensions(image_url)
        if img_size:
            width, height = img_size

    if width and height:
        width, height = _convert_pixel_to_percentage(width, height, hotspot.get('x'), hotspot.get('y'))
        hotspot.set('x', width)
        hotspot.set('y', height)


def _get_image_dimensions(image_url):
    try:
        img = Image.open(urllib.urlopen(image_url))
    except Exception as e:
        logger.warning('Failed loading image: {}'.format(e.message))
        return None
    else:
        return img.size


def _replace_static_from_url(url, course_id):
    if not url:
        return url
    try:
        from static_replace import replace_static_urls
    except ImportError:
        return url

    url = '"{}"'.format(url)
    lms_relative_url = replace_static_urls(url, course_id=course_id)
    lms_relative_url = lms_relative_url.strip('"')
    return _make_url_absolute(lms_relative_url)


def _make_url_absolute(url):
    lms_base = settings.ENV_TOKENS.get('LMS_BASE')
    scheme = 'https' if settings.HTTPS == 'on' else 'http'
    lms_base = '{}://{}'.format(scheme, lms_base)
    return urljoin(lms_base, url)


def _convert_pixel_to_percentage(width, height, x_in_pixel, y_in_pixel):
    x_in_percentage = (float(x_in_pixel) / width) * 100
    y_in_percentage = (float(y_in_pixel) / height) * 100

    return '{}%'.format(x_in_percentage), '{}%'.format(y_in_percentage)


def _save_updated_block(self, block):
    store.update_item(xblock=block, user_id=self.staff_user_id)