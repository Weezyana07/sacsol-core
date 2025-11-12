# core/schema.py
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.utils import extend_schema

@extend_schema(summary="OpenAPI schema", description="Download the OpenAPI (YAML/JSON) schema.", auth=[])
class PublicSchemaView(SpectacularAPIView):
    pass