from __future__ import annotations

import csv
import os
import shutil

from xlsxwriter.workbook import Workbook

from ..stats import add_stats_sheet, stats_from_csv
from .csv_writer import CSVWriter


class XLSXWriter(CSVWriter):
    """Writer (post-process converter) to XLSX table."""

    def __exit__(self, *exc_info) -> None:
        super().__exit__(*exc_info)

        # Convert csv to xlsx table
        csv_opts = self._options.csv
        stats = (stats_from_csv(self._file_path, csv_opts.join_char, self._options.encoding)
                 if csv_opts.add_site_flag and csv_opts.add_rubrics else None)
        tmp_xlx_name = os.path.splitext(self._file_path)[0] + '.converted.xlsx'
        with Workbook(tmp_xlx_name) as workbook:
            bold = workbook.add_format({'bold': True})  # Add header format

            worksheet = workbook.add_worksheet()
            with self._open_file(self._file_path, 'r') as f_csv:
                csv_reader = csv.reader(f_csv)
                for r, row in enumerate(csv_reader):
                    for c, col in enumerate(row):
                        if r == 0:
                            worksheet.write(r, c, col, bold)  # Write header
                        else:
                            worksheet.write(r, c, col)

            if stats is not None:
                add_stats_sheet(workbook, stats)

        # Replace original table with new one
        shutil.move(tmp_xlx_name, self._file_path)
