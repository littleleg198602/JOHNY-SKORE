from __future__ import annotations

from pathlib import Path

import pandas as pd


class ExcelExporter:
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
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            signals.to_excel(writer, sheet_name="Signals", index=False)
            sources.to_excel(writer, sheet_name="Sources", index=False)
            articles.to_excel(writer, sheet_name="Articles", index=False)

            dashboard_sheet = pd.concat(
                [
                    dashboard["top_total"].assign(section="Top20 TotalScore"),
                    dashboard["weekly_drops"].assign(section="Top20 Weekly Drops"),
                    dashboard["m1_drops"].assign(section="Top20 1M Drops"),
                    dashboard["m3_drops"].assign(section="Top20 3M Drops"),
                ],
                ignore_index=True,
            )
            dashboard_sheet.to_excel(writer, sheet_name="Dashboard", index=False)
            if delta is not None and not delta.empty:
                delta.to_excel(writer, sheet_name="DeltaVsPrev", index=False)
        return output_path
