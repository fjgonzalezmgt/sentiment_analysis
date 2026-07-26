import pandas as pd
from chromadb.api.types import validate_where

from dashboard import chroma_where


def test_chroma_where_uses_chroma_compound_filter_format():
    """Build valid Chroma expressions when dashboard filters narrow the data."""
    dataframe = pd.DataFrame(
        {
            "company": ["DHL", "DHL"],
            "sentiment_label": ["Negativa", "Negativa"],
            "general_category": ["Entrega", "Entrega"],
        }
    )

    where = chroma_where(dataframe)

    assert where == {
        "$and": [
            {"company": {"$eq": "DHL"}},
            {"sentiment_label": {"$eq": "Negativa"}},
            {"general_category": {"$eq": "Entrega"}},
        ]
    }
    validate_where(where)


def test_chroma_where_omits_unselected_filters():
    """Avoid restricting a semantic search when every dashboard value remains."""
    dataframe = pd.DataFrame(
        {
            "company": ["DHL", "ALIEXPRESS"],
            "sentiment_label": ["Negativa", "Positiva"],
            "general_category": ["Entrega", "Experiencia general"],
        }
    )

    assert chroma_where(dataframe) is None