"""Pagination matching the frontend contract: ``{ items, total, page, pageSize }``."""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class PageNumberWithSizePagination(PageNumberPagination):
    page_query_param = "page"
    page_size_query_param = "pageSize"
    page_size = 10
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "items": data,
                "total": self.page.paginator.count,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),  # camelised to pageSize by the renderer
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["items", "total", "page", "pageSize"],
            "properties": {
                "items": schema,
                "total": {"type": "integer", "example": 123},
                "page": {"type": "integer", "example": 1},
                "pageSize": {"type": "integer", "example": 10},
            },
        }
