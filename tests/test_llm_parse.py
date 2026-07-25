import json
from types import SimpleNamespace

import pandas as pd
import pytest

import llm_parse


def test_review_classification_enforces_polarity_taxonomy():
    """Verificar la coherencia entre polaridad y categoría.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    valid = llm_parse.ReviewClassification(
        sentiment_label="Positiva",
        general_category="Entrega",
        specific_category="Entrega rápida",
    )
    assert valid.validate_taxonomy() is valid

    invalid = llm_parse.ReviewClassification(
        sentiment_label="Positiva",
        general_category="Entrega",
        specific_category="Retraso en la entrega",
    )
    with pytest.raises(ValueError, match="no corresponde"):
        invalid.validate_taxonomy()


def test_prepare_batch_files_builds_responses_jsonl(tmp_path):
    """Verificar la estructura JSONL usada por Responses Batch API.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Directorio temporal proporcionado por Pytest.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    source = tmp_path / "reviews.csv"
    pd.DataFrame(
        [
            {"review_id": "a", "body": "Entrega rápida", "company": "ACME"},
            {"review_id": "b", "body": "Nunca llegó", "company": "ACME"},
        ]
    ).to_csv(source, index=False)
    requests_path = tmp_path / "requests.jsonl"
    manifest_path = tmp_path / "manifest.json"

    _, _, count = llm_parse.prepare_batch_files(
        str(source),
        str(requests_path),
        str(manifest_path),
        model="gpt-5.6-luna",
    )

    lines = [json.loads(line) for line in requests_path.read_text(encoding="utf-8").splitlines()]
    assert count == 2
    assert len(lines) == 2
    assert lines[0]["url"] == "/v1/responses"
    assert lines[0]["body"]["model"] == "gpt-5.6-luna"
    assert lines[0]["body"]["text"]["format"]["strict"] is True
    assert len(json.loads(manifest_path.read_text(encoding="utf-8"))) == 2


def test_response_output_text_reads_batch_response():
    """Verificar la extracción del texto desde una respuesta Batch.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    body = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"sentiment_label":"Positiva"}',
                    }
                ],
            }
        ]
    }
    assert "Positiva" in llm_parse._response_output_text(body)


def test_finalize_batch_rebuilds_tabular_output(tmp_path, monkeypatch):
    """Verificar la reconstrucción tabular de un Batch terminado.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Directorio temporal proporcionado por Pytest.
    monkeypatch : pytest.MonkeyPatch
        Fixture usada para sustituir el cliente externo.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "review-1": {
                    "review_id": "1",
                    "body": "Entrega rápida",
                    "company": "ACME",
                }
            }
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "batch_id": "batch_test",
                "manifest_path": str(manifest_path),
            }
        ),
        encoding="utf-8",
    )
    classification = {
        "sentiment_label": "Positiva",
        "general_category": "Entrega",
        "specific_category": "Entrega rápida",
    }
    response_line = {
        "custom_id": "review-1",
        "response": {
            "status_code": 200,
            "body": {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(classification),
                            }
                        ],
                    }
                ]
            },
        },
    }

    class FakeFiles:
        def content(self, _file_id):
            """Devolver el contenido simulado de un archivo.

            Parameters
            ----------
            _file_id : str
                Identificador ignorado por la simulación.

            Returns
            -------
            types.SimpleNamespace
                Objeto con el texto JSONL.
            """
            return SimpleNamespace(text=json.dumps(response_line))

    client = SimpleNamespace(
        batches=SimpleNamespace(
            retrieve=lambda _batch_id: SimpleNamespace(
                id="batch_test",
                status="completed",
                output_file_id="file_output",
                error_file_id=None,
            )
        ),
        files=FakeFiles(),
    )
    monkeypatch.setattr(llm_parse, "get_openai_client", lambda: client)

    result = llm_parse.finalize_batch(
        str(state_path),
        output_xlsx=str(tmp_path / "classified.xlsx"),
        output_csv=str(tmp_path / "classified.csv"),
    )

    assert result is not None
    assert result.loc[0, "sentiment_label"] == "Positiva"
    assert result.loc[0, "specific_category"] == "Entrega rápida"
