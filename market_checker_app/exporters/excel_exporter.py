from __future__ import annotations

from pathlib import Path

import pandas as pd


class ExcelExporter:
    @staticmethod
    def _sanitize_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
        cleaned = frame.copy()
        for column in cleaned.columns:
            series = cleaned[column]
            if pd.api.types.is_datetime64tz_dtype(series):
                cleaned[column] = series.dt.tz_localize(None)
                continue

            if series.dtype == object:
                sample = series.dropna()
                if not sample.empty:
                    first = sample.iloc[0]
                    if isinstance(first, pd.Timestamp) and first.tzinfo is not None:
                        cleaned[column] = pd.to_datetime(series, errors="coerce").dt.tz_localize(None)
        return cleaned

    def export(
        self,
        output_path: Path,
        signals: pd.DataFrame,
        sources: pd.DataFrame,
        articles: pd.DataFrame,
        dashboard: dict[str, pd.DataFrame],
        delta: pd.DataFrame | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        signals_xlsx = self._sanitize_for_excel(signals)
        sources_xlsx = self._sanitize_for_excel(sources)
        articles_xlsx = self._sanitize_for_excel(articles)
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            signals_xlsx.to_excel(writer, sheet_name="Signals", index=False)
            sources_xlsx.to_excel(writer, sheet_name="Sources", index=False)
            articles_xlsx.to_excel(writer, sheet_name="Articles", index=False)

            dashboard_sheet = pd.concat(
                [
                    dashboard["top_total"].assign(section="Top20 TotalScore"),
                    dashboard["weekly_drops"].assign(section="Top20 Weekly Drops"),
                    dashboard["m1_drops"].assign(section="Top20 1M Drops"),
                    dashboard["m3_drops"].assign(section="Top20 3M Drops"),
                ],
                ignore_index=True,
            )
            self._sanitize_for_excel(dashboard_sheet).to_excel(writer, sheet_name="Dashboard", index=False)
            if delta is not None and not delta.empty:
                self._sanitize_for_excel(delta).to_excel(writer, sheet_name="DeltaVsPrev", index=False)
        return output_path
