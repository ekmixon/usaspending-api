import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from elasticsearch import Elasticsearch


logger = logging.getLogger("console")


TEST_INDEX_NAME_PATTERN = "test-*"


class Command(BaseCommand):
    def handle(self, *args, **options):
        client = Elasticsearch([settings.ES_HOSTNAME], timeout=settings.ES_TIMEOUT)
        response = client.indices.delete(TEST_INDEX_NAME_PATTERN)
        if response.get("acknowledged") is True:
            logger.info(
                f"All Elasticsearch indexes matching '{TEST_INDEX_NAME_PATTERN}' have been dropped from {settings.ES_HOSTNAME}... probably."
            )

        else:
            logger.warning(
                f"Attempted to drop All Elasticsearch indexes matching '{TEST_INDEX_NAME_PATTERN}' from {settings.ES_HOSTNAME} but did not receive a positive acknowledgment.  Is that a problem?  ¯\\_(ツ)_/¯"
            )
