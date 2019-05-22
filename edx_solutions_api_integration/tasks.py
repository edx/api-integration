import logging

from lxml import etree
from celery.task import task

from xmodule.modulestore.django import modulestore
from opaque_keys.edx.keys import CourseKey
from edx_solutions_api_integration.utils import (
    replace_static_from_url,
    get_image_dimensions,
)


logger = logging.getLogger('edx.celery.task')
store = modulestore()


@task(name=u'lms.djangoapps.api_integration.tasks.update_image_explorer_schema')
def update_image_explorer_schema(staff_user_id, course_ids, revert=False):
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
                if revert:
                    _revert_upgrade_schema(block, course_id, staff_user_id)
                else:
                    _upgrade_xml_schema(block, course_id, staff_user_id)


def _revert_upgrade_schema(block, course_id, staff_user_id):
    xmltree = etree.fromstring(block.data)
    script_processed = xmltree.attrib.get('script_processed', False)

    if not script_processed:
        return

    logger.info('Reverting back IE schema for block `{}` in course `{}`'.format(block.parent.block_id, course_id))
    xmltree.set('schema_version', '1')
    xmltree.attrib.pop('script_processed')  # remove processed marker
    hotspots_element = xmltree.find('hotspots')
    hotspot_elements = hotspots_element.findall('hotspot')

    for index, hotspot_element in enumerate(hotspot_elements):
        if hotspot_element.get('x').endswith('%') or hotspot_element.get('y').endswith('%'):
            _convert_to_percentage_coordinates(xmltree, hotspot_element, course_id, revert=True)

    block.data = etree.tostring(xmltree)
    store.update_item(xblock=block, user_id=staff_user_id)
    logger.info('Successfully reverted IE schema for block `{}` in course: `{}`'
                .format(block.parent.block_id, course_id))


def _upgrade_xml_schema(block, course_id, staff_user_id):
    xmltree = etree.fromstring(block.data)
    schema_version = int(xmltree.attrib.get('schema_version', 1))

    if schema_version > 1:
        return

    hotspots_element = xmltree.find('hotspots')
    hotspot_elements = hotspots_element.findall('hotspot')
    update_schema = False

    for index, hotspot_element in enumerate(hotspot_elements):
        if not hotspot_element.get('x').endswith('%') or not hotspot_element.get('y').endswith('%'):
            update_schema = True
            _convert_to_percentage_coordinates(xmltree, hotspot_element, course_id)

    if update_schema:
        xmltree.set('schema_version', '2')
        # mark this as updated from script for tracking purpose
        xmltree.set('script_processed', '1')
        block.data = etree.tostring(xmltree)
        store.update_item(xblock=block, user_id=staff_user_id)
        logger.info('Successfully Updated IE schema for block `{}` in course: `{}`'
                    .format(block.parent.block_id, course_id))


def _convert_to_percentage_coordinates(xmltree, hotspot, course_id, revert=False):
    background = xmltree.find('background')
    width = background.get('width')
    height = background.get('height')

    if None in (width, height):
        image_url = replace_static_from_url(background.get('src'), course_id=course_id)
        img_size = get_image_dimensions(image_url)
        if img_size:
            width, height = img_size

    if width and height:
        if revert:
            width, height = _convert_percentage_to_pixels(width, height, hotspot.get('x'), hotspot.get('y'))
        else:
            width, height = _convert_pixel_to_percentage(width, height, hotspot.get('x'), hotspot.get('y'))
        hotspot.set('x', width)
        hotspot.set('y', height)


def _convert_pixel_to_percentage(width, height, x_in_pixel, y_in_pixel):
    x_in_percentage = ((float(x_in_pixel) + 20.5) / width) * 100
    y_in_percentage = ((float(y_in_pixel) + 20.5) / height) * 100

    return '{}%'.format(x_in_percentage), '{}%'.format(y_in_percentage)


def _convert_percentage_to_pixels(width, height, x_in_percent, y_in_percent):
    x_in_percent = x_in_percent.replace('%', '')
    y_in_percent = y_in_percent.replace('%', '')

    x_in_px= (float(x_in_percent) * width) / 100
    y_in_px = (float(y_in_percent) * height) / 100

    return str(x_in_px - 20.5), str(y_in_px - 20.5)
