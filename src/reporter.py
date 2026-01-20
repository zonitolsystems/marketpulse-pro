"""Report generation module with Excel and interactive HTML dashboards.

This module transforms extracted data into actionable insights through:
- Excel exports for raw data distribution
- Interactive Plotly dashboards for data visualization
- Summary statistics for stakeholder reporting

Design Rationale:
    Beyond raw data extraction, enterprise clients expect data intelligence.
    The ReportGenerator demonstrates the ability to create polished,
    self-contained deliverables that require no additional tooling to consume.

    Plotly was chosen for interactive visualizations due to its ability
    to produce standalone HTML files with embedded JavaScript, enabling
    stakeholders to explore data without installing Python or BI tools.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config.settings import GlobalConfig, get_config
from src.exceptions import ReportGenerationError
from src.extractor import ExtractionResult
from src.logger import get_logger
from src.validator import ProductSchema

log = get_logger(__name__)


class ReportGenerator:
    """Generates Excel and HTML reports from extraction results.

    Transforms raw extraction data into multiple output formats:
    - Excel workbook with raw data and summary sheets
    - Interactive HTML dashboard with embedded Plotly charts

    Attributes:
        config: GlobalConfig instance for output paths.
        _timestamp: Report generation timestamp for file naming.

    Example:
        reporter = ReportGenerator()
        excel_path = reporter.generate_excel(result)
        html_path = reporter.generate_dashboard(result)
    """

    def __init__(self, config: GlobalConfig | None = None) -> None:
        """Initialize ReportGenerator with configuration.

        Args:
            config: Optional GlobalConfig. Uses singleton if not provided.
        """
        self.config = config or get_config()
        self._timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    def _ensure_output_dir(self) -> Path:
        """Ensure output directory exists and return path.

        Returns:
            Path to the output directory.

        Raises:
            ReportGenerationError: If directory cannot be created.
        """
        try:
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            return self.config.output_dir
        except OSError as exc:
            raise ReportGenerationError(
                report_type="output_directory",
                reason=f"Cannot create output directory: {exc}",
                output_path=str(self.config.output_dir),
            ) from exc

    def _result_to_dataframe(self, result: ExtractionResult[ProductSchema]) -> pd.DataFrame:
        """Convert extraction result to pandas DataFrame.

        Args:
            result: ExtractionResult containing validated items.

        Returns:
            DataFrame with product data.
        """
        records = [
            {
                "title": item.title,
                "price": item.price,
                "stock": "In Stock" if item.stock else "Out of Stock",
                "stock_bool": item.stock,
                "rating": item.rating,
                "url": str(item.url),
            }
            for item in result.items
        ]
        return pd.DataFrame(records)

    def generate_excel(
        self,
        result: ExtractionResult[ProductSchema],
        filename: str | None = None,
    ) -> Path:
        """Generate Excel workbook with raw data and summary.

        Creates a multi-sheet workbook containing:
        - Raw Data: All extracted items
        - Summary: Aggregated statistics
        - Price Analysis: Price distribution metrics

        Args:
            result: ExtractionResult containing validated items.
            filename: Optional custom filename (without extension).

        Returns:
            Path to the generated Excel file.

        Raises:
            ReportGenerationError: If Excel generation fails.
        """
        output_dir = self._ensure_output_dir()
        filename = filename or f"marketpulse_export_{self._timestamp}"
        output_path = output_dir / f"{filename}.xlsx"

        log.info("Generating Excel report", output_path=str(output_path))

        try:
            df = self._result_to_dataframe(result)

            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                # Sheet 1: Raw Data
                df_export = df[["title", "price", "stock", "rating", "url"]]
                df_export.to_excel(writer, sheet_name="Raw Data", index=False)

                # Sheet 2: Summary Statistics
                summary_data = self._generate_summary_stats(result, df)
                summary_df = pd.DataFrame([summary_data])
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

                # Sheet 3: Price Analysis
                price_analysis = self._generate_price_analysis(df)
                price_df = pd.DataFrame([price_analysis])
                price_df.to_excel(writer, sheet_name="Price Analysis", index=False)

                # Sheet 4: Rating Distribution
                rating_dist = df.groupby("rating").size().reset_index(name="count")
                rating_dist.to_excel(writer, sheet_name="Rating Distribution", index=False)

            log.info(
                "Excel report generated successfully",
                output_path=str(output_path),
                total_items=len(result.items),
            )
            return output_path

        except Exception as exc:
            raise ReportGenerationError(
                report_type="Excel",
                reason=str(exc),
                output_path=str(output_path),
            ) from exc

    def _generate_summary_stats(
        self,
        result: ExtractionResult[ProductSchema],
        df: pd.DataFrame,
    ) -> dict[str, Any]:
        """Generate summary statistics dictionary.

        Args:
            result: ExtractionResult with metadata.
            df: DataFrame with product data.

        Returns:
            Dictionary of summary statistics.
        """
        return {
            "Report Generated": datetime.now(UTC).isoformat(),
            "Source URL": result.source_url,
            "Total Items": len(result.items),
            "Pages Scraped": result.pages_scraped,
            "Success Rate": f"{result.success_rate:.1%}",
            "In Stock Count": df["stock_bool"].sum(),
            "Out of Stock Count": len(df) - df["stock_bool"].sum(),
            "Average Price": f"£{df['price'].mean():.2f}" if len(df) > 0 else "N/A",
            "Average Rating": f"{df['rating'].mean():.1f}" if len(df) > 0 else "N/A",
        }

    def _generate_price_analysis(self, df: pd.DataFrame) -> dict[str, Any]:
        """Generate price analysis statistics.

        Args:
            df: DataFrame with product data.

        Returns:
            Dictionary of price statistics.
        """
        if len(df) == 0:
            return {"message": "No data available"}

        return {
            "Minimum Price": f"£{df['price'].min():.2f}",
            "Maximum Price": f"£{df['price'].max():.2f}",
            "Mean Price": f"£{df['price'].mean():.2f}",
            "Median Price": f"£{df['price'].median():.2f}",
            "Std Deviation": f"£{df['price'].std():.2f}",
            "Price Range": f"£{df['price'].max() - df['price'].min():.2f}",
        }

    def generate_dashboard(
        self,
        result: ExtractionResult[ProductSchema],
        filename: str | None = None,
    ) -> Path:
        """Generate interactive HTML dashboard with Plotly.

        Creates a standalone HTML file containing:
        - Price Distribution Histogram
        - Availability Pie Chart
        - Top 10 Highest Rated Items Bar Chart
        - Summary statistics header

        Args:
            result: ExtractionResult containing validated items.
            filename: Optional custom filename (without extension).

        Returns:
            Path to the generated HTML file.

        Raises:
            ReportGenerationError: If dashboard generation fails.
        """
        output_dir = self._ensure_output_dir()
        filename = filename or f"marketpulse_dashboard_{self._timestamp}"
        output_path = output_dir / f"{filename}.html"

        log.info("Generating HTML dashboard", output_path=str(output_path))

        try:
            df = self._result_to_dataframe(result)

            if len(df) == 0:
                raise ReportGenerationError(
                    report_type="Dashboard",
                    reason="No data available for visualization",
                    output_path=str(output_path),
                )

            # Create subplot figure
            fig = make_subplots(
                rows=2,
                cols=2,
                subplot_titles=(
                    "Price Distribution",
                    "Stock Availability",
                    "Top 10 Highest Rated Books",
                    "Rating Distribution",
                ),
                specs=[
                    [{"type": "histogram"}, {"type": "pie"}],
                    [{"type": "bar"}, {"type": "bar"}],
                ],
                vertical_spacing=0.12,
                horizontal_spacing=0.1,
            )

            # Chart 1: Price Distribution Histogram
            fig.add_trace(
                go.Histogram(
                    x=df["price"],
                    nbinsx=20,
                    name="Price",
                    marker_color="#3498db",
                    hovertemplate="Price Range: £%{x}<br>Count: %{y}<extra></extra>",
                ),
                row=1,
                col=1,
            )

            # Chart 2: Stock Availability Pie Chart
            stock_counts = df["stock"].value_counts()
            fig.add_trace(
                go.Pie(
                    labels=stock_counts.index.tolist(),
                    values=stock_counts.values.tolist(),
                    name="Availability",
                    marker_colors=["#27ae60", "#e74c3c"],
                    hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
                ),
                row=1,
                col=2,
            )

            # Chart 3: Top 10 Highest Rated (by rating, then by price)
            top_rated = (
                df.sort_values(["rating", "price"], ascending=[False, False])
                .head(10)
                .iloc[::-1]  # Reverse for horizontal bar chart
            )
            fig.add_trace(
                go.Bar(
                    y=top_rated["title"].str[:30] + "...",
                    x=top_rated["rating"],
                    orientation="h",
                    name="Rating",
                    marker_color="#9b59b6",
                    text=top_rated["rating"],
                    textposition="auto",
                    hovertemplate=("<b>%{y}</b><br>Rating: %{x} stars<br><extra></extra>"),
                ),
                row=2,
                col=1,
            )

            # Chart 4: Rating Distribution Bar Chart
            rating_dist = df.groupby("rating").size().reset_index(name="count")
            fig.add_trace(
                go.Bar(
                    x=rating_dist["rating"],
                    y=rating_dist["count"],
                    name="Count",
                    marker_color="#f39c12",
                    text=rating_dist["count"],
                    textposition="auto",
                    hovertemplate="Rating: %{x} stars<br>Count: %{y}<extra></extra>",
                ),
                row=2,
                col=2,
            )

            # Update layout
            fig.update_layout(
                title={
                    "text": (
                        f"<b>MarketPulse-Pro Dashboard</b><br>"
                        f"<sup>Source: {result.source_url} | "
                        f"Items: {len(result.items)} | "
                        f"Pages: {result.pages_scraped} | "
                        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}</sup>"
                    ),
                    "x": 0.5,
                    "xanchor": "center",
                },
                showlegend=False,
                height=800,
                template="plotly_white",
                font={"family": "Arial, sans-serif"},
            )

            # Update axes labels
            fig.update_xaxes(title_text="Price (£)", row=1, col=1)
            fig.update_yaxes(title_text="Count", row=1, col=1)
            fig.update_xaxes(title_text="Rating (Stars)", row=2, col=1)
            fig.update_xaxes(title_text="Rating", row=2, col=2)
            fig.update_yaxes(title_text="Count", row=2, col=2)

            # Write to HTML file
            fig.write_html(
                str(output_path),
                include_plotlyjs=True,
                full_html=True,
            )

            log.info(
                "HTML dashboard generated successfully",
                output_path=str(output_path),
                total_items=len(result.items),
            )
            return output_path

        except ReportGenerationError:
            raise
        except Exception as exc:
            raise ReportGenerationError(
                report_type="Dashboard",
                reason=str(exc),
                output_path=str(output_path),
            ) from exc

    def generate_all(
        self,
        result: ExtractionResult[ProductSchema],
    ) -> dict[str, Path]:
        """Generate all report types.

        Convenience method to generate both Excel and HTML reports
        in a single call.

        Args:
            result: ExtractionResult containing validated items.

        Returns:
            Dictionary mapping report type to file path.
        """
        return {
            "excel": self.generate_excel(result),
            "dashboard": self.generate_dashboard(result),
        }
