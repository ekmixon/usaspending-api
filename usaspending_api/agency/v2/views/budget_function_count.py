from django.db.models import Exists, OuterRef, Q
from rest_framework.request import Request
from rest_framework.response import Response
from typing import Any
from usaspending_api.accounts.models import TreasuryAppropriationAccount
from usaspending_api.agency.v2.views.agency_base import AgencyBase
from usaspending_api.common.cache_decorator import cache_response
from usaspending_api.financial_activities.models import FinancialAccountsByProgramActivityObjectClass


class BudgetFunctionCount(AgencyBase):
    """
    Obtain the count of budget functions for a specific agency in a single
    fiscal year based on whether or not that budget function has ever
    been submitted in File B.
    """

    endpoint_doc = "usaspending_api/api_contracts/contracts/v2/agency/toptier_code/budget_function/count.md"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params_to_validate = ["fiscal_year"]

    @cache_response()
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        rows = list(self.get_budget_function_queryset())

        return Response(
            {
                "toptier_code": self.toptier_code,
                "fiscal_year": self.fiscal_year,
                "budget_function_count": len(
                    {row["budget_function_code"] for row in rows}
                ),
                "budget_sub_function_count": len(
                    {row["budget_subfunction_code"] for row in rows}
                ),
                "messages": self.standard_response_messages,
            }
        )

    def get_budget_function_queryset(self):
        filters = [
            Q(treasury_account_id=OuterRef("pk")),
            Q(final_of_fy=True),
            Q(treasury_account__funding_toptier_agency=self.toptier_agency),
            Q(submission__reporting_fiscal_year=self.fiscal_year),
            Q(
                Q(obligations_incurred_by_program_object_class_cpe__gt=0)
                | Q(obligations_incurred_by_program_object_class_cpe__lt=0)
                | Q(gross_outlay_amount_by_program_object_class_cpe__gt=0)
                | Q(gross_outlay_amount_by_program_object_class_cpe__lt=0)
            ),
        ]
        return (
            TreasuryAppropriationAccount.objects.annotate(
                include=Exists(FinancialAccountsByProgramActivityObjectClass.objects.filter(*filters).values("pk"))
            )
            .filter(include=True)
            .values("budget_function_code", "budget_subfunction_code")
        )
