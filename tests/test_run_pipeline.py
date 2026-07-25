import sys

import pytest

import run_pipeline


def test_parse_args_accepts_combined_csv(monkeypatch):
    """Verificar el punto de entrada desde un CSV consolidado.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture usada para sustituir los argumentos de proceso.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--from-combined-csv", "output/reviews.csv"],
    )

    args = run_pipeline.parse_args()

    assert args.from_combined_csv == "output/reviews.csv"
    assert args.llm_mode == "batch"


def test_classification_stage_rejects_missing_csv(tmp_path):
    """Verificar que el nuevo punto de entrada valida su archivo.

    Parameters
    ----------
    tmp_path : pathlib.Path
        Directorio temporal proporcionado por Pytest.

    Returns
    -------
    None
        La prueba finaliza mediante aserciones.
    """
    with pytest.raises(FileNotFoundError, match="CSV consolidado"):
        run_pipeline.run_classification_stage(
            str(tmp_path / "missing.csv"),
            str(tmp_path / "output"),
            "batch",
        )
