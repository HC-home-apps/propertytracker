import pytest
import tempfile
import os

from tracker.db import Database

@pytest.fixture
def temp_db():
    """Provide a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        yield f.name
    os.unlink(f.name)

@pytest.fixture
def db():
    """Provide an initialised Database instance backed by a temp file."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    database = Database(path)
    database.init_schema()
    yield database
    database.close()
    os.unlink(path)

@pytest.fixture
def sample_config():
    """Provide sample configuration for tests."""
    return {
        'savings': {
            'current_balance': 150000,
            'monthly_contribution': 5000,
        },
        'ppor': {
            'debt': 400000,
            'selling_cost_rate': 0.02,
        },
        'investment_property': {
            'debt': 600000,
            'refinance_lvr_cap': 0.80,
            'valuation_haircut': {
                'bear': 0.90,
                'base': 0.95,
                'bull': 1.00,
            },
        },
        'thresholds': {
            'min_sample_monthly': 3,
            'min_sample_quarterly': 5,
            'min_sample_6month': 8,
        },
    }
