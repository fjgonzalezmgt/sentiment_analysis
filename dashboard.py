"""Panel interactivo para analizar reseñas clasificadas y buscar en Chroma."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = BASE_DIR / "output" / "resenas_clasificadas.xlsx"
FALLBACK_DATA_PATH = BASE_DIR / "output_test" / "resenas_clasificadas.xlsx"
REQUIRED_COLUMNS = {
    "review",
    "sentiment_label",
    "general_category",
    "specific_category",
    "company",
}
SENTIMENT_COLORS = {"Positiva": "#1B9E77", "Negativa": "#D95F02"}


def _read_reviews(path_value: str) -> pd.DataFrame:
    """Load a classified review file and normalize dashboard fields."""
    path = Path(path_value)
    dataframe = (
        pd.read_csv(path, encoding="utf-8-sig")
        if path.suffix.lower() == ".csv"
        else pd.read_excel(path)
    )
    missing = REQUIRED_COLUMNS.difference(dataframe.columns)
    if missing:
        raise ValueError(
            "El archivo no contiene las columnas clasificadas requeridas: "
            + ", ".join(sorted(missing))
        )

    dataframe = dataframe.copy()
    for column in REQUIRED_COLUMNS:
        dataframe[column] = dataframe[column].fillna("Sin clasificar").astype(str)
    dataframe["rating"] = pd.to_numeric(dataframe.get("rating"), errors="coerce")
    dataframe["published_at"] = pd.to_datetime(
        dataframe.get("date_published"), errors="coerce", utc=True
    )
    dataframe["published_date"] = dataframe["published_at"].dt.date
    return dataframe


@st.cache_data(show_spinner=False)
def load_reviews(path_value: str, modified_at: float) -> pd.DataFrame:
    """Cache the source while invalidating it when the file changes."""
    del modified_at
    return _read_reviews(path_value)


def choose_data_source() -> Path | None:
    """Return the production data source, then the test fixture as a fallback."""
    for path in (DEFAULT_DATA_PATH, FALLBACK_DATA_PATH):
        if path.exists():
            return path
    return None


def filter_reviews(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Render filtering controls and return the matching records."""
    st.sidebar.header("Filtros")
    companies = st.sidebar.multiselect(
        "Empresas", sorted(dataframe["company"].unique()), default=None
    )
    sentiments = st.sidebar.multiselect(
        "Sentimiento", sorted(dataframe["sentiment_label"].unique()), default=None
    )
    categories = st.sidebar.multiselect(
        "Categorías generales", sorted(dataframe["general_category"].unique()), default=None
    )

    ratings = dataframe["rating"].dropna()
    minimum_rating = float(ratings.min()) if not ratings.empty else 0.0
    maximum_rating = float(ratings.max()) if not ratings.empty else 5.0
    selected_rating = st.sidebar.slider(
        "Valoración", minimum_rating, maximum_rating, (minimum_rating, maximum_rating), 0.5
    )

    dates = dataframe["published_date"].dropna()
    selected_dates = None
    if not dates.empty:
        selected_dates = st.sidebar.date_input(
            "Periodo", value=(dates.min(), dates.max()), min_value=dates.min(), max_value=dates.max()
        )

    filtered = dataframe.copy()
    if companies:
        filtered = filtered[filtered["company"].isin(companies)]
    if sentiments:
        filtered = filtered[filtered["sentiment_label"].isin(sentiments)]
    if categories:
        filtered = filtered[filtered["general_category"].isin(categories)]
    filtered = filtered[filtered["rating"].isna() | filtered["rating"].between(*selected_rating)]
    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        filtered = filtered[
            filtered["published_date"].isna()
            | filtered["published_date"].between(selected_dates[0], selected_dates[1])
        ]
    return filtered


def chart_layout(figure: go.Figure, height: int = 340) -> go.Figure:
    """Apply the dashboard visual language to every Plotly chart."""
    return figure.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=42, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Source Sans 3, sans-serif", color="#25323A"),
        legend_title_text="",
    ).update_xaxes(gridcolor="#E4E9E7").update_yaxes(gridcolor="#E4E9E7")


def chroma_where(dataframe: pd.DataFrame) -> dict[str, Any] | None:
    """Build an optional Chroma metadata filter from the active dashboard slice."""
    conditions: list[dict[str, Any]] = []
    for column in ("company", "sentiment_label", "general_category"):
        values = dataframe[column].dropna().unique().tolist()
        if len(values) == 1:
            conditions.append({column: {"$eq": values[0]}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def render_overview(dataframe: pd.DataFrame) -> None:
    """Render KPI and comparison views for the selected review set."""
    total = len(dataframe)
    negative_share = (dataframe["sentiment_label"] == "Negativa").mean() * 100 if total else 0
    average_rating = dataframe["rating"].mean()
    top_issue = (
        dataframe.loc[dataframe["sentiment_label"] == "Negativa", "specific_category"]
        .value_counts()
        .index
    )
    metrics = st.columns(4)
    metrics[0].metric("Reseñas analizadas", f"{total:,}")
    metrics[1].metric("Sentimiento negativo", f"{negative_share:.1f}%")
    metrics[2].metric("Valoración media", f"{average_rating:.2f}/5" if pd.notna(average_rating) else "Sin dato")
    metrics[3].metric("Principal fricción", "Ver detalle" if len(top_issue) else "Sin dato")
    if len(top_issue):
        metrics[3].caption(top_issue[0])

    left, right = st.columns(2)
    with left:
        sentiment = dataframe.groupby("sentiment_label").size().reset_index(name="Reseñas")
        figure = px.bar(
            sentiment,
            x="sentiment_label",
            y="Reseñas",
            color="sentiment_label",
            color_discrete_map=SENTIMENT_COLORS,
            title="Distribución de sentimiento",
        )
        st.plotly_chart(chart_layout(figure), use_container_width=True)
    with right:
        by_company = (
            dataframe.groupby(["company", "sentiment_label"]).size().reset_index(name="Reseñas")
        )
        figure = px.bar(
            by_company,
            x="company",
            y="Reseñas",
            color="sentiment_label",
            barmode="group",
            color_discrete_map=SENTIMENT_COLORS,
            title="Sentimiento por empresa",
        )
        st.plotly_chart(chart_layout(figure), use_container_width=True)

    chronology = dataframe.dropna(subset=["published_date"]).groupby(
        ["published_date", "sentiment_label"]
    ).size().reset_index(name="Reseñas")
    if not chronology.empty:
        figure = px.area(
            chronology,
            x="published_date",
            y="Reseñas",
            color="sentiment_label",
            color_discrete_map=SENTIMENT_COLORS,
            title="Evolución temporal",
        )
        st.plotly_chart(chart_layout(figure, 300), use_container_width=True)


def render_categories(dataframe: pd.DataFrame) -> None:
    """Render category concentration and company/category heatmap."""
    negative = dataframe[dataframe["sentiment_label"] == "Negativa"]
    top_categories = negative["specific_category"].value_counts().head(12).sort_values()
    figure = px.bar(
        top_categories,
        orientation="h",
        title="Principales motivos de insatisfacción",
        labels={"value": "Reseñas", "specific_category": "Categoría específica"},
        color_discrete_sequence=["#D95F02"],
    )
    st.plotly_chart(chart_layout(figure, 440), use_container_width=True)

    matrix = pd.crosstab(dataframe["company"], dataframe["general_category"])
    if not matrix.empty:
        figure = go.Figure(
            go.Heatmap(
                z=matrix.values,
                x=matrix.columns,
                y=matrix.index,
                colorscale="YlGnBu",
                colorbar_title="Reseñas",
                hovertemplate="Empresa: %{y}<br>Categoría: %{x}<br>Reseñas: %{z}<extra></extra>",
            )
        )
        figure.update_layout(title="Concentración temática por empresa")
        st.plotly_chart(chart_layout(figure, 360), use_container_width=True)


def render_evidence(dataframe: pd.DataFrame) -> None:
    """Render review-level evidence, keeping the source text accessible."""
    st.dataframe(
        dataframe[
            [
                "company",
                "sentiment_label",
                "general_category",
                "specific_category",
                "rating",
                "published_date",
                "review",
            ]
        ].sort_values(["company", "rating"], ascending=[True, True]),
        use_container_width=True,
        hide_index=True,
        column_config={"review": st.column_config.TextColumn("Reseña", width="large")},
    )


def render_semantic_search(dataframe: pd.DataFrame) -> None:
    """Query Chroma with the search term and the active metadata slice."""
    query = st.text_input("Buscar por significado", placeholder="Ej.: retrasos, falta de respuesta o paquetes dañados")
    limit = st.slider("Resultados", 3, 20, 8)
    if not query.strip():
        return
    try:
        from chroma_store import semantic_search

        results = semantic_search(query.strip(), n_results=limit, where=chroma_where(dataframe))
    except Exception as error:
        st.error(f"No se pudo consultar Chroma: {error}")
        return
    if not results:
        st.info("No se encontraron reseñas semánticamente similares.")
        return
    for result in results:
        title = f"{result.get('company', 'Empresa')} | {result.get('sentiment_label', 'Sin sentimiento')}"
        with st.expander(title):
            st.write(result["review"])
            st.caption(
                f"Categoría: {result.get('general_category', 'Sin dato')} / "
                f"{result.get('specific_category', 'Sin dato')} | "
                f"Distancia: {result['distance']:.3f}"
            )


def main() -> None:
    """Run the Streamlit application."""
    st.set_page_config(page_title="Radar de Reseñas", page_icon="R", layout="wide")
    st.title("Radar de Reseñas")
    st.caption("Análisis de percepción del cliente y evidencia semántica")
    st.sidebar.title("Radar de Reseñas")
    source = choose_data_source()
    uploaded = st.sidebar.file_uploader("Archivo clasificado", type=["csv", "xlsx"])
    if uploaded is not None:
        uploaded_path = BASE_DIR / ".streamlit_upload" / uploaded.name
        uploaded_path.parent.mkdir(exist_ok=True)
        uploaded_path.write_bytes(uploaded.getvalue())
        source = uploaded_path
    if source is None:
        st.error("No se encontró un archivo de reseñas clasificadas en output/ u output_test/.")
        st.stop()
    try:
        dataframe = load_reviews(str(source), source.stat().st_mtime)
    except Exception as error:
        st.error(f"No se pudo leer {source.name}: {error}")
        st.stop()
    filtered = filter_reviews(dataframe)
    st.sidebar.caption(f"Fuente: {source.relative_to(BASE_DIR) if source.is_relative_to(BASE_DIR) else source.name}")
    if filtered.empty:
        st.warning("Los filtros no devuelven reseñas. Ajusta la selección para continuar.")
        st.stop()

    overview_tab, category_tab, evidence_tab, semantic_tab = st.tabs(
        ["Panorama", "Categorías", "Evidencia", "Búsqueda semántica"]
    )
    with overview_tab:
        render_overview(filtered)
    with category_tab:
        render_categories(filtered)
    with evidence_tab:
        render_evidence(filtered)
    with semantic_tab:
        render_semantic_search(filtered)


if __name__ == "__main__":
    main()