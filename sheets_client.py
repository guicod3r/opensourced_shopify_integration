"""Google Sheets API client."""
import logging
from google.auth import default
from googleapiclient.discovery import build

from order_mapper import dict_to_row, Col, COLUMN_ORDER

logger = logging.getLogger(__name__)

_PEDIDO_COL = chr(ord('A') + COLUMN_ORDER.index(Col.PEDIDO))   # C
_ESTADO_COL = chr(ord('A') + COLUMN_ORDER.index(Col.ESTADO))   # D
_ESTADO_IDX = COLUMN_ORDER.index(Col.ESTADO)
_PAGO_COL = chr(ord('A') + COLUMN_ORDER.index(Col.PAGO))       # E
_PAGO_IDX = COLUMN_ORDER.index(Col.PAGO)


class SheetsClient:
    def __init__(self, spreadsheet_id: str, sheet_name: str):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        creds, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.service = build("sheets", "v4", credentials=creds)

    def _find_order_row_indices(self, order_name: str) -> list[int]:
        """Return 1-based row numbers where PEDIDO column matches order_name."""
        result = (
            self.service.spreadsheets().values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"{self.sheet_name}!{_PEDIDO_COL}:{_PEDIDO_COL}")
            .execute()
        )
        rows = result.get("values", [])
        return [i + 1 for i, row in enumerate(rows) if row and row[0] == order_name]

    def _find_first_empty_row(self) -> int:
        """Finds the first empty row by checking the FECHA column using binary search (O(log n)).
        
        Preconditions:
        - Rows are always inserted contiguously.
        - There are no empty rows between populated rows.
        - The FECHA column is populated for every valid row.
        - Once an empty FECHA cell is found, all following rows are also empty.
        """
        _FECHA_COL = chr(ord('A') + COLUMN_ORDER.index(Col.FECHA))
        result = (
            self.service.spreadsheets().values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"{self.sheet_name}!{_FECHA_COL}:{_FECHA_COL}")
            .execute()
        )
        values = result.get("values", [])

        inicio = 0
        fin = len(values) - 1
        primera_vacia = len(values)

        while inicio <= fin:
            medio = (inicio + fin) // 2
            val = values[medio][0] if values[medio] else ""
            if not val or not str(val).strip():
                primera_vacia = medio
                fin = medio - 1
            else:
                inicio = medio + 1

        return primera_vacia + 1

    def _append_dicts(self, dicts: list[dict]):
        rows = [dict_to_row(d) for d in dicts]
        if not rows:
            return

        first_empty = self._find_first_empty_row()

        # Retrieve sheet metadata to check dimensions
        sheet_metadata = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        sheet_id = 0
        total_rows = 0
        for sheet in sheet_metadata.get('sheets', []):
            if sheet.get("properties", {}).get("title") == self.sheet_name:
                props = sheet.get("properties", {})
                sheet_id = props.get("sheetId", 0)
                total_rows = props.get("gridProperties", {}).get("rowCount", 0)
                break

        # Extend grid if not enough space
        if first_empty + len(rows) - 1 > total_rows:
            logger.info(f"Row {first_empty} exceeds grid limits. Extending grid by {len(rows)} rows.")
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [{
                        "appendDimension": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "length": len(rows)
                        }
                    }]
                }
            ).execute()

        result = (
            self.service.spreadsheets().values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.sheet_name}!A{first_empty}",
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            )
            .execute()
        )
        logger.info(f"Inserted {len(rows)} rows at row {first_empty}: {result.get('updatedCells', 0)} cells updated")

    def upsert_dicts(self, dicts: list[dict]):
        """Append new order. If existing, updates only ESTADO and PAGO to preserve manual edits."""
        if not dicts:
            return

        order_name = dicts[0].get(Col.PEDIDO, "")
        existing = self._find_order_row_indices(order_name)

        if not existing:
            self._append_dicts(dicts)
            logger.info(f"Inserted {len(dicts)} rows for new order {order_name}")
            return

        rows = [dict_to_row(d) for d in dicts]
        data = []
        for i, row_idx in enumerate(existing):
            if i >= len(rows):
                break
            data.append({
                "range": f"{self.sheet_name}!{_ESTADO_COL}{row_idx}",
                "values": [[rows[i][_ESTADO_IDX]]],
            })
            data.append({
                "range": f"{self.sheet_name}!{_PAGO_COL}{row_idx}",
                "values": [[rows[i][_PAGO_IDX]]],
            })
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()
        logger.info(f"Updated ESTADO+PAGO for {len(existing)} rows of order {order_name}")