import pandas as pd

import chroma_store


class FakeCollection:
    def __init__(self):
        """Inicializar una colección simulada.

        Returns
        -------
        None
            El constructor conserva las llamadas recibidas.
        """
        self.calls = []

    def upsert(self, **kwargs):
        """Registrar una operación de upsert simulada.

        Parameters
        ----------
        **kwargs : Any
            Argumentos enviados a la colección.

        Returns
        -------
        None
            La operación solo registra los argumentos.
        """
        self.calls.append(kwargs)


def test_metadata_sanitization_and_stable_id():
    """Verificar saneamiento de metadatos e identidad determinista.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    row = {
        "review_id": "123",
        "company": "ACME",
        "review": "Buen servicio",
        "rating": 5,
        "empty": None,
    }
    metadata = chroma_store.sanitize_metadata(row)
    assert metadata["company"] == "ACME"
    assert metadata["rating"] == 5
    assert "review" not in metadata
    assert "empty" not in metadata
    assert chroma_store.stable_review_id(row) == chroma_store.stable_review_id(row)


def test_upsert_reviews_uses_embedding_batches(tmp_path):
    """Verificar que los upserts respetan el tamaño de lote.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Directorio temporal proporcionado por Pytest.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    source = tmp_path / "classified.csv"
    pd.DataFrame(
        [
            {"review_id": "1", "review": "Excelente", "sentiment_label": "Positiva"},
            {"review_id": "2", "review": "Muy tarde", "sentiment_label": "Negativa"},
            {"review_id": "3", "review": "Buen trato", "sentiment_label": "Positiva"},
        ]
    ).to_csv(source, index=False)
    collection = FakeCollection()

    count = chroma_store.upsert_reviews(
        str(source),
        batch_size=2,
        collection=collection,
    )

    assert count == 3
    assert [len(call["ids"]) for call in collection.calls] == [2, 1]
