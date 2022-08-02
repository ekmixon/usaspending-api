import logging
import re
import shutil
import subprocess
import time

from calendar import monthrange, isleap
from datetime import datetime as dt
from dateutil import parser

from django.conf import settings
from django.db import connection
from fiscalyear import datetime
from usaspending_api.common.matview_manager import (
    OVERLAY_VIEWS,
    DEPENDENCY_FILEPATH,
    MATERIALIZED_VIEWS,
    CHUNKED_MATERIALIZED_VIEWS,
    MATVIEW_GENERATOR_FILE,
    DEFAULT_MATIVEW_DIR,
)
from usaspending_api.references.models import Agency


logger = logging.getLogger(__name__)
TEMP_SQL_FILES = [DEFAULT_MATIVEW_DIR / val["sql_filename"] for val in MATERIALIZED_VIEWS.values()]
TEMP_SQL_FILES += [DEFAULT_MATIVEW_DIR / val["sql_filename"] for val in CHUNKED_MATERIALIZED_VIEWS.values()]


def read_text_file(filepath):
    with open(filepath, "r") as plaintext_file:
        file_content_str = plaintext_file.read()
    return file_content_str


def convert_string_to_datetime(input: str) -> datetime.datetime:
    """Parse a string into a datetime object"""
    return parser.parse(input)


def convert_string_to_date(input: str) -> datetime.date:
    """Parse a string into a date object"""
    return convert_string_to_datetime(input).date()


def validate_date(date):
    if not isinstance(date, (datetime.datetime, datetime.date)):
        raise TypeError("Incorrect parameter type provided")

    if not (date.day or date.month or date.year):
        raise Exception("Malformed date object provided")


def check_valid_toptier_agency(agency_id):
    """ Check if the ID provided (corresponding to Agency.id) is a valid toptier agency """
    agency = Agency.objects.filter(id=agency_id, toptier_flag=True).first()
    return agency is not None


def generate_date_from_string(date_str):
    """ Expects a string with format YYYY-MM-DD. returns datetime.date """
    try:
        return datetime.date(*[int(x) for x in date_str.split("-")])
    except Exception as e:
        logger.error(str(e))
    return None


def dates_are_month_bookends(start, end):
    try:
        last_day_of_month = monthrange(end.year, end.month)[1]
        if start.day == 1 and end.day == last_day_of_month:
            return True
    except Exception as e:
        logger.error(str(e))
    return False


def min_and_max_from_date_ranges(filter_time_periods: list) -> tuple:
    min_date = min(
        t.get("start_date", settings.API_MAX_DATE) for t in filter_time_periods
    )

    max_date = max(
        t.get("end_date", settings.API_SEARCH_MIN_DATE)
        for t in filter_time_periods
    )

    return dt.strptime(min_date, "%Y-%m-%d"), dt.strptime(max_date, "%Y-%m-%d")


def within_one_year(d1, d2):
    """ includes leap years """
    year_range = list(range(d1.year, d2.year + 1))
    if len(year_range) > 2:
        return False
    days_diff = abs((d2 - d1).days)
    for leap_year in [year for year in year_range if isleap(year)]:
        leap_date = datetime.datetime(leap_year, 2, 29)
        if d1 <= leap_date <= d2:
            days_diff -= 1
    return days_diff <= 365


EXTRACT_MATVIEW_SQL = re.compile(r"^.*?CREATE MATERIALIZED VIEW (.*?)_temp\b(.*?) (?:NO )?WITH DATA;.*?$", re.DOTALL)
REPLACE_VIEW_SQL = r"CREATE OR REPLACE VIEW \1\2;"


def convert_matview_to_view(matview_sql):
    sql = EXTRACT_MATVIEW_SQL.sub(REPLACE_VIEW_SQL, matview_sql)
    if sql == matview_sql:
        raise RuntimeError(
            "Error converting materialized view to traditional view.  Perhaps the structure of matviews has changed?"
        )
    return sql


def generate_matviews(materialized_views_as_traditional_views=False):
    with connection.cursor() as cursor:
        cursor.execute(CREATE_READONLY_SQL)
        cursor.execute(DEPENDENCY_FILEPATH.read_text())
        subprocess.call(f"python3 {MATVIEW_GENERATOR_FILE} --dest {DEFAULT_MATIVEW_DIR} --quiet", shell=True)
        for matview_sql_file in TEMP_SQL_FILES:
            sql = matview_sql_file.read_text()
            if materialized_views_as_traditional_views:
                sql = convert_matview_to_view(sql)
            cursor.execute(sql)
        for view_sql_file in OVERLAY_VIEWS:
            cursor.execute(view_sql_file.read_text())

    shutil.rmtree(DEFAULT_MATIVEW_DIR)


def get_pagination(results, limit, page, benchmarks=False):
    if benchmarks:
        start_pagination = time.time()
    page_metadata = {
        "page": page,
        "count": len(results),
        "next": None,
        "previous": None,
        "hasNext": False,
        "hasPrevious": False,
    }
    if limit < 1 or page < 1:
        return [], page_metadata

    page_metadata["hasNext"] = limit * page < len(results)
    page_metadata["hasPrevious"] = page > 1 and limit * (page - 2) < len(results)

    paginated_results = (
        results[limit * (page - 1) : limit * page]
        if page_metadata["hasNext"]
        else results[limit * (page - 1) :]
    )

    page_metadata["next"] = page + 1 if page_metadata["hasNext"] else None
    page_metadata["previous"] = page - 1 if page_metadata["hasPrevious"] else None
    if benchmarks:
        logger.info(f"get_pagination took {time.time() - start_pagination} seconds")
    return paginated_results, page_metadata


def get_pagination_metadata(total_return_count, limit, page):
    page_metadata = {
        "page": page,
        "total": total_return_count,
        "limit": limit,
        "next": None,
        "previous": None,
        "hasNext": False,
        "hasPrevious": False,
    }
    if limit < 1 or page < 1:
        return page_metadata

    page_metadata["hasNext"] = limit * page < total_return_count
    page_metadata["hasPrevious"] = page > 1 and limit * (page - 2) < total_return_count
    page_metadata["next"] = page + 1 if page_metadata["hasNext"] else None
    page_metadata["previous"] = page - 1 if page_metadata["hasPrevious"] else None
    return page_metadata


def get_simple_pagination_metadata(results_plus_one, limit, page):
    has_next = results_plus_one > limit
    has_previous = page > 1

    return {
        "page": page,
        "next": page + 1 if has_next else None,
        "previous": page - 1 if has_previous else None,
        "hasNext": has_next,
        "hasPrevious": has_previous,
    }


def get_generic_filters_message(original_filters, allowed_filters):
    retval = [get_time_period_message()]
    if set(original_filters).difference(allowed_filters):
        retval.append(unused_filters_message(set(original_filters).difference(allowed_filters)))
    return retval


def get_time_period_message():
    return (
        "For searches, time period start and end dates are currently limited to an earliest date of "
        f"{settings.API_SEARCH_MIN_DATE}.  For data going back to {settings.API_MIN_DATE}, use either the Custom "
        "Award Download feature on the website or one of our download or bulk_download API endpoints as "
        "listed on https://api.usaspending.gov/docs/endpoints."
    )


def unused_filters_message(filters):
    return f"The following filters from the request were not used: {filters}. See https://api.usaspending.gov/docs/endpoints for a list of appropriate filters"


def get_account_data_time_period_message():
    return 'Account data powering this endpoint were first collected in FY2017 Q2 under the DATA Act; as such, there are no data available for prior fiscal years.'


# Raw SQL run during a migration
FY_PG_FUNCTION_DEF = """
    CREATE OR REPLACE FUNCTION fy(raw_date DATE)
    RETURNS integer AS $$
          DECLARE result INTEGER;
          DECLARE month_num INTEGER;
          BEGIN
            month_num := EXTRACT(MONTH from raw_date);
            result := EXTRACT(YEAR FROM raw_date);
            IF month_num > 9
            THEN
              result := result + 1;
            END IF;
            RETURN result;
          END;
        $$ LANGUAGE plpgsql;

    CREATE OR REPLACE FUNCTION fy(raw_date TIMESTAMP WITH TIME ZONE)
    RETURNS integer AS $$
          DECLARE result INTEGER;
          DECLARE month_num INTEGER;
          BEGIN
            month_num := EXTRACT(MONTH from raw_date);
            result := EXTRACT(YEAR FROM raw_date);
            IF month_num > 9
            THEN
              result := result + 1;
            END IF;
            RETURN result;
          END;
        $$ LANGUAGE plpgsql;


    CREATE OR REPLACE FUNCTION fy(raw_date TIMESTAMP WITHOUT TIME ZONE)
    RETURNS integer AS $$
          DECLARE result INTEGER;
          DECLARE month_num INTEGER;
          BEGIN
            month_num := EXTRACT(MONTH from raw_date);
            result := EXTRACT(YEAR FROM raw_date);
            IF month_num > 9
            THEN
              result := result + 1;
            END IF;
            RETURN result;
          END;
        $$ LANGUAGE plpgsql;
        """

FY_FROM_TEXT_PG_FUNCTION_DEF = """
    CREATE OR REPLACE FUNCTION fy(raw_date TEXT)
    RETURNS integer AS $$
          BEGIN
            RETURN fy(raw_date::DATE);
          END;
        $$ LANGUAGE plpgsql;
        """
"""
Filtering on `field_name__fy` is present for free on all Date fields.
To add this field to the serializer:

1. Add tests that the field is present and functioning
(see awards/tests/test_awards.py)

2. Add a SerializerMethodField and `def get_field_name__fy`
to the serializer (see awards/serializers.py)

3. add field_name`__fy` to the model's `get_default_fields`
(see awards/models.py)

Also, query performance will stink unless/until the field is indexed.

CREATE INDEX ON awards(FY(field_name))
"""

CREATE_READONLY_SQL = """DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly') THEN
CREATE ROLE readonly;
END IF;
END$$;"""


def generate_test_db_connection_string():
    db = connection.cursor().db.settings_dict
    return f'postgres://{db["USER"]}:{db["PASSWORD"]}@{db["HOST"]}:5432/{db["NAME"]}'


def sort_with_null_last(to_sort, sort_key, sort_order, tie_breaker=None):
    """
    Use tuples to sort results so that None can be converted to a Boolean for comparison
    """
    if tie_breaker is None:
        tie_breaker = sort_key
    return sorted(
        to_sort,
        key=lambda x: ((x[sort_key] is None) == (sort_order == "asc"), x[sort_key], x[tie_breaker]),
        reverse=(sort_order == "desc"),
    )
