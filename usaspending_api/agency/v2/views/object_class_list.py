from django.db.models import Q, Sum, F
from rest_framework.request import Request
from rest_framework.response import Response
from typing import Any, List
from usaspending_api.agency.v2.views.agency_base import AgencyBase, PaginationMixin
from usaspending_api.common.cache_decorator import cache_response
from usaspending_api.common.helpers.generic_helper import get_pagination_metadata
from usaspending_api.references.models import ObjectClass


class ObjectClassList(PaginationMixin, AgencyBase):
    """
    Obtain the list of object classes for a specific agency in a single
    fiscal year based on whether or not that object class has ever
    been submitted in File B.
    """

    endpoint_doc = "usaspending_api/api_contracts/contracts/v2/agency/toptier_code/object_class.md"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params_to_validate = ["fiscal_year", "filter"]

    @cache_response()
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        self.sortable_columns = ["name", "obligated_amount", "gross_outlay_amount"]
        self.default_sort_column = "obligated_amount"
        results = list(self.get_object_class_list())
        page_metadata = get_pagination_metadata(len(results), self.pagination.limit, self.pagination.page)
        results = results[self.pagination.lower_limit : self.pagination.upper_limit]
        return Response(
            {
                "toptier_code": self.toptier_code,
                "fiscal_year": self.fiscal_year,
                "page_metadata": page_metadata,
                "results": results[: self.pagination.limit],
                "messages": self.standard_response_messages,
            }
        )

    def get_object_class_list(self) -> List[dict]:
        filters = [
            Q(financialaccountsbyprogramactivityobjectclass__final_of_fy=True),
            Q(
                financialaccountsbyprogramactivityobjectclass__treasury_account__funding_toptier_agency=self.toptier_agency
            ),
            Q(financialaccountsbyprogramactivityobjectclass__submission__reporting_fiscal_year=self.fiscal_year),
            Q(
                Q(financialaccountsbyprogramactivityobjectclass__obligations_incurred_by_program_object_class_cpe__gt=0)
                | Q(
                    financialaccountsbyprogramactivityobjectclass__obligations_incurred_by_program_object_class_cpe__lt=0
                )
                | Q(
                    financialaccountsbyprogramactivityobjectclass__gross_outlay_amount_by_program_object_class_cpe__gt=0
                )
                | Q(
                    financialaccountsbyprogramactivityobjectclass__gross_outlay_amount_by_program_object_class_cpe__lt=0
                )
            ),
        ]
        if self.filter:
            filters.append(Q(object_class_name__icontains=self.filter))
        return (
            ObjectClass.objects.filter(*filters)
            .annotate(
                name=F("object_class_name"),
                obligated_amount=Sum(
                    "financialaccountsbyprogramactivityobjectclass__obligations_incurred_by_program_object_class_cpe"
                ),
                gross_outlay_amount=Sum(
                    "financialaccountsbyprogramactivityobjectclass__gross_outlay_amount_by_program_object_class_cpe"
                ),
            )
            .order_by(
                f"{'-' if self.pagination.sort_order == 'desc' else ''}{self.pagination.sort_key}"
            )
            .values("name", "obligated_amount", "gross_outlay_amount")
        )
