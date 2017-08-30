""" API implementation for Secure api calls. """

import socket
import struct
import json
import re
import datetime
import logging

from django.core.cache import cache
from django.utils.timezone import now
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta, MO
from django.conf import settings

from rest_framework.exceptions import ParseError

from student.roles import CourseRole, CourseObserverRole


USER_METRICS_CACHE_TTL = 60 * 60
COURSE_METRICS_CACHE_TTL = 60 * 60


log = logging.getLogger(__name__)


def address_exists_in_network(ip_address, net_n_bits):
    """
    return True if the ip address exists in the subnet address
    otherwise return False
    """
    ip_address = struct.unpack('<L', socket.inet_aton(ip_address))[0]
    net, bits = net_n_bits.split('/')
    net_address = struct.unpack('<L', socket.inet_aton(net))[0]
    net_mask = ((1L << int(bits)) - 1)
    return ip_address & net_mask == net_address & net_mask


def get_client_ip_address(request):
    """
    get the client IP Address
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip_address = x_forwarded_for.split(',')[-1].strip()
    else:
        ip_address = request.META.get('REMOTE_ADDR')
    return ip_address


def str2bool(value):
    """
    convert string to bool
    """
    if value:
        return value.lower() in ("true",)
    else:
        return False


def generate_base_uri(request, strip_qs=False):
    """
    Build absolute uri
    """
    if strip_qs:
        return request.build_absolute_uri(request.path)  # Don't need querystring that why giving location parameter
    else:
        return request.build_absolute_uri()


def is_int(value):
    """
    checks if a string value can be interpreted as integer
    """
    try:
        int(value)
        return True
    except ValueError:
        return False


def dict_has_items(obj, items):
    """
    examine a `obj` for given `items`. if all `items` are found in `obj`
    return True otherwise false. where `obj` is a dictionary and `items`
    is list of dictionaries
    """
    has_items = False
    if isinstance(obj, basestring):
        obj = json.loads(obj)
    for item in items:
        for lookup_key, lookup_val in item.iteritems():
            if lookup_key in obj and obj[lookup_key] == lookup_val:
                has_items = True
            else:
                return False
    return has_items


def get_cache_key(category, course_id, user_id=None):
    """
    :param category: string which represents type of cache data e.g. `grade`, `progress`, `social_score`
    :param course_id: string course_id of course for which data is being cached
    :param user_id: int user_id of user for which data is being cached
    :return:
    """

    return u"edx_solutions_api_integration.{category}.{course_id}.{user_id}".format(
        category=category,
        course_id=unicode(course_id),
        user_id=user_id,
    )


def get_cached_data(category, course_id, user_id=None):
    """
    Fetches cached data for a given metric, course and user
    If user_id is given, it makes sure both course and user data is available, if either
    course or user data is not available it returns None
    """
    metric_course_key = get_cache_key(category, course_id)
    metric_course_data = cache.get(metric_course_key)
    if user_id:
        metric_cache_key = get_cache_key(category, course_id, user_id)
        metric_user_data = cache.get(metric_cache_key)

        if isinstance(metric_course_data, dict) and isinstance(metric_user_data, dict):
            metric_course_data.update(metric_user_data)
            return metric_course_data
    else:
        return metric_course_data


def cache_course_data(category, course_id, data):
    """
    caches course data for a given metric and course
    """
    metric_cache_key = get_cache_key(category, course_id)
    cache.set(metric_cache_key, data, COURSE_METRICS_CACHE_TTL)


def cache_course_user_data(category, course_id, user_id, data):
    """
    caches user data for a given metric and course
    """
    metric_cache_key = get_cache_key(category, course_id, user_id)
    cache.set(metric_cache_key, data, USER_METRICS_CACHE_TTL)


def invalid_user_data_cache(category, course_id, user_id=None):
    """
    Invalidates course and user's data cache for a category in given course
    :param category:
    :param course_id:
    :param user_id:
    """
    course_cache_key = get_cache_key(category, course_id)
    cache.delete(course_cache_key)
    if user_id:
        user_cache_key = get_cache_key(category, course_id, user_id)
        cache.delete(user_cache_key)


def get_aggregate_exclusion_user_ids(course_key, roles=None):  # pylint: disable=invalid-name
    """
    This helper method will return the list of user ids that are marked in roles
    that can be excluded from certain aggregate queries. The list of roles to exclude
    can either be passed in roles argument or defined in a AGGREGATION_EXCLUDE_ROLES settings variable.
    """

    cache_key = get_cache_key('exclude_users', unicode(course_key) + '_'.join(roles or 'None'))
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data
    exclude_user_ids = set()
    exclude_role_list = roles or getattr(settings, 'AGGREGATION_EXCLUDE_ROLES', [CourseObserverRole.ROLE])

    for role in exclude_role_list:
        users = CourseRole(role, course_key).users_with_role()
        user_ids = set()
        for user in users:
            user_ids.add(user.id)

        exclude_user_ids = exclude_user_ids.union(user_ids)

    cache.set(cache_key, exclude_user_ids, 60 * 60)
    return exclude_user_ids


def extract_data_params(request):
    """
    extracts all query params which starts with data__
    """
    data_params = []
    for key, val in request.query_params.iteritems():
        if key.startswith('data__'):
            data_params.append({key[6:]: val})
    return data_params


def strip_time(dt):  # pylint: disable=C0103
    """
    Removes time part of datetime
    """
    tzinfo = getattr(dt, 'tzinfo', now().tzinfo) or now().tzinfo
    return datetime.datetime(dt.year, dt.month, dt.day, tzinfo=tzinfo)


def to_mysql_datetime(dt):  # pylint: disable=C0103
    """
    convert python datetime to mysql compatible datetime
    """
    return datetime.datetime.strftime(dt, '%Y-%m-%d %H:%M:%S')


def parse_datetime(date_val, defaultdt=None):
    """
    Parses datetime value from string
    """
    if isinstance(date_val, basestring):
        return parse(date_val, yearfirst=True, default=defaultdt)
    return date_val


def get_interval_bounds(date_val, interval):
    """
    Returns interval bounds the datetime is in.
    """

    day = strip_time(date_val)

    if interval == 'day':
        begin = day
        end = day + relativedelta(days=1)
    elif interval == 'week':
        begin = day - relativedelta(weekday=MO(-1))
        end = begin + datetime.timedelta(days=7)
    elif interval == 'month':
        begin = strip_time(datetime.datetime(date_val.year, date_val.month, 1, tzinfo=date_val.tzinfo))
        end = begin + relativedelta(months=1)
    end = end - relativedelta(microseconds=1)
    return begin, end


def detect_db_engine():
    """
    detects database engine used
    """
    engine = 'mysql'
    backend = settings.DATABASES['default']['ENGINE']
    if 'sqlite' in backend:
        engine = 'sqlite'
    return engine


def get_time_series_data(queryset, start, end, interval='days', date_field='created', date_field_model=None,
                         aggregate=None):
    """
    Aggregate over time intervals to compute time series representation of data
    """
    engine = detect_db_engine()
    start, _ = get_interval_bounds(start, interval.rstrip('s'))
    _, end = get_interval_bounds(end, interval.rstrip('s'))

    if date_field_model:
        date_field = '`{}`.`{}`'.format(date_field_model._meta.db_table, date_field)  # pylint: disable=W0212

    sql = {
        'mysql': {
            'days': "DATE_FORMAT({}, '%%Y-%%m-%%d')".format(date_field),
            'weeks': "DATE_FORMAT(DATE_SUB({}, INTERVAL(WEEKDAY({})) DAY), '%%Y-%%m-%%d')".format(date_field,
                                                                                                  date_field),
            'months': "DATE_FORMAT({}, '%%Y-%%m-01')".format(date_field)
        },
        'sqlite': {
            'days': "strftime('%%Y-%%m-%%d', {})".format(date_field),
            'weeks': "strftime('%%Y-%%m-%%d', julianday({}) - strftime('%%w', {}) + 1)".format(date_field,
                                                                                               date_field),
            'months': "strftime('%%Y-%%m-01', {})".format(date_field)
        }
    }
    interval_sql = sql[engine][interval]
    where_clause = '{} BETWEEN "{}" AND "{}"'.format(date_field,
                                                     to_mysql_datetime(start) if engine == 'mysql' else start,
                                                     to_mysql_datetime(end) if engine == 'mysql' else end)
    aggregate_data = queryset.extra(select={'d': interval_sql}, where=[where_clause]).order_by().values('d').\
        annotate(agg=aggregate)

    today = strip_time(now())
    data = dict((strip_time(parse_datetime(item['d'], today)), item['agg']) for item in aggregate_data)

    series = []
    dt_key = start
    while dt_key < end:
        value = data.get(dt_key, 0)
        series.append((dt_key, value,))
        dt_key += relativedelta(**{interval: 1})

    return series


def get_ids_from_list_param(request, param_name):
    """
    Returns list of ids extracted from query param
    """
    ids = request.query_params.get(param_name, None)
    if ids:
        upper_bound = getattr(settings, 'API_LOOKUP_UPPER_BOUND', 100)
        try:
            ids = map(int, ids.split(','))[:upper_bound]
        except Exception:
            raise ParseError("Invalid {} parameter value".format(param_name))

    return ids


def strip_xblock_wrapper_div(html):
    """
    Removes xblock wrapper div from given html
    """
    match = re.search(
        r'^<div class=\"xblock xblock-student_view(.+?)</script>(?:\n?)(.+?)(?:\n?)</div>$', html, re.DOTALL
    )
    if match:
        return match.group(2).strip(' ')
    else:
        return html


def strip_whitespaces_and_newlines(string):
    """
    Removes whitespaces and newline characters from string
    """
    string = string.replace('\n', '')
    return string.strip()


def has_api_key_permission(request):
    """
    Checks if request has api key permisssion
    """
    # If settings.DEBUG is True and settings.EDX_API_KEY is not set or None,
    # then allow the request. Otherwise, allow the request if and only if
    # settings.EDX_API_KEY is set and the X-Edx-Api-Key HTTP header is
    # present in the request and matches the setting.
    debug_enabled = settings.DEBUG
    api_key = getattr(settings, 'EDX_API_KEY', None)

    # DEBUG mode rules over all else
    # Including the api_key check here ensures we don't break the feature locally
    if debug_enabled and api_key is None:
        log.warn("EDX_API_KEY Override: Debug Mode")
        return True

    # If we're not DEBUG, we need a local api key
    if api_key is None:
        return False

    # The client needs to present the same api key
    header_key = request.META.get('HTTP_X_EDX_API_KEY')
    if header_key is None:
        try:
            header_key = request.META['headers'].get('X-Edx-Api-Key')
        except KeyError:
            return False
        if header_key is None:
            return False

    # The api key values need to be the same
    if header_key != api_key:
        return False

    # Allow the request to take place
    return True
