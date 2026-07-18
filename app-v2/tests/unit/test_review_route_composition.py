"""Production app review-route composition."""

from fastapi.openapi.models import OpenAPI

from quantinue.core.config import Settings
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app


def test_postgres_app_composes_due_review_route() -> None:
    # Given/When
    app = create_app(
        Settings.model_validate(
            {
                "database_mode": "postgres",
                "database_url": "postgresql+asyncpg://test:test@127.0.0.1:55400/test",
            }
        ),
        store=InMemoryRunStore(),
    )

    # Then
    schema = OpenAPI.model_validate(app.openapi())
    assert schema.paths is not None
    assert "/api/reviews/{signal_id}/process" in schema.paths
