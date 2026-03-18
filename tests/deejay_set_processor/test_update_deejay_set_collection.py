from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from deejay_set_processor.update_deejay_set_collection import generate_dj_set_collection


def _make_folder(name: str, fid: str):
    return SimpleNamespace(name=name, id=fid)


def _make_file(name: str, fid: str, mime: str):
    return SimpleNamespace(name=name, id=fid, mime_type=mime)


def test_generate_dj_set_collection_snapshot_shape():
    summary_folder = _make_folder("Summary", "folder-summary")
    year_2023 = _make_folder("2023", "folder-2023")
    year_2024 = _make_folder("2024", "folder-2024")
    archive = _make_folder("Archive", "folder-archive")

    drive = SimpleNamespace(
        find_or_create_spreadsheet=MagicMock(return_value="spreadsheet-id"),
        get_all_subfolders=MagicMock(
            return_value=[year_2023, archive, summary_folder, year_2024]
        ),
        get_files_in_folder=MagicMock(
            side_effect=lambda folder_id, **_: _files_for_folder(folder_id)
        ),
    )

    formatter = SimpleNamespace(
        apply_formatting_to_sheet=MagicMock(),
        set_column_text_formatting=MagicMock(),
        reorder_sheets=MagicMock(),
    )
    sheets = SimpleNamespace(
        formatter=formatter,
        clear_all_except_one_sheet=MagicMock(),
        insert_rows=MagicMock(),
        delete_sheet_by_name=MagicMock(),
        get_metadata=MagicMock(return_value={"sheets": []}),
    )
    fake_g = SimpleNamespace(drive=drive, sheets=sheets)

    def _files_for_folder(folder_id: str):
        if folder_id == "folder-2024":
            return [
                _make_file(
                    "2024-01-01_First Set",
                    "sheet-2024-1",
                    "application/vnd.google-apps.spreadsheet",
                ),
                _make_file(
                    "2024-02-01_Second Set",
                    "sheet-2024-2",
                    "application/vnd.google-apps.spreadsheet",
                ),
            ]
        if folder_id == "folder-2023":
            return []
        if folder_id == "folder-summary":
            return [
                _make_file(
                    "2024 Summary",
                    "sheet-summary-2024",
                    "application/vnd.google-apps.spreadsheet",
                ),
            ]
        if folder_id == "folder-archive":
            return [
                _make_file(
                    "ignored.csv",
                    "sheet-ignored",
                    "application/vnd.google-apps.spreadsheet",
                ),
            ]
        return []

    with (
        patch(
            "deejay_set_processor.update_deejay_set_collection.GoogleAPI"
        ) as mock_google_api,
        patch(
            "deejay_set_processor.update_deejay_set_collection.write_json_snapshot"
        ) as mock_write_snapshot,
        patch("deejay_set_processor.update_deejay_set_collection.log"),
        patch(
            "deejay_set_processor.update_deejay_set_collection.config"
        ) as mock_config,
        patch(
            "deejay_set_processor.update_deejay_set_collection.create_collection_snapshot"
        ) as mock_create_snapshot,
    ):
        mock_google_api.from_env.return_value = fake_g
        mock_config.DJ_SETS_FOLDER_ID = "dj-sets-folder"
        mock_config.OUTPUT_NAME = "DJ Set Collection"
        mock_config.TEMP_TAB_NAME = "Temp"
        mock_config.SUMMARY_TAB_NAME = "Summary"
        mock_create_snapshot.return_value = {"folders": []}

        generate_dj_set_collection()

        mock_write_snapshot.assert_called_once()
        snapshot, path = mock_write_snapshot.call_args.args

    assert path.endswith("v1/deejay-sets/deejay_set_collection.json")
    assert "folders" in snapshot

    folder_names = [f["name"] for f in snapshot["folders"]]
    assert "Archive" not in folder_names
    assert folder_names == ["Summary", "2024", "2023"]

    summary_folder_entry = next(
        f for f in snapshot["folders"] if f["name"] == "Summary"
    )
    for item in summary_folder_entry["items"]:
        assert set(item.keys()) == {"label", "url", "spreadsheet_id"}

    year_2024_entry = next(f for f in snapshot["folders"] if f["name"] == "2024")
    assert len(year_2024_entry["items"]) == 2
    for item in year_2024_entry["items"]:
        assert set(item.keys()) == {"date", "title", "label", "url", "spreadsheet_id"}

    year_2023_entry = next(f for f in snapshot["folders"] if f["name"] == "2023")
    assert year_2023_entry["items"] == []


def test_generate_dj_set_collection_handles_write_failure_and_continues():
    parent_folder = _make_folder("2024", "folder-2024")
    drive = SimpleNamespace(
        find_or_create_spreadsheet=MagicMock(return_value="spreadsheet-id"),
        get_all_subfolders=MagicMock(return_value=[parent_folder]),
        get_files_in_folder=MagicMock(return_value=[]),
    )
    formatter = SimpleNamespace(
        apply_formatting_to_sheet=MagicMock(),
        reorder_sheets=MagicMock(),
    )
    sheets = SimpleNamespace(
        formatter=formatter,
        clear_all_except_one_sheet=MagicMock(),
        insert_rows=MagicMock(),
        delete_sheet_by_name=MagicMock(),
        get_metadata=MagicMock(return_value={"sheets": []}),
    )
    fake_g = SimpleNamespace(drive=drive, sheets=sheets)

    with (
        patch(
            "deejay_set_processor.update_deejay_set_collection.GoogleAPI"
        ) as mock_google_api,
        patch(
            "deejay_set_processor.update_deejay_set_collection.write_json_snapshot",
            side_effect=RuntimeError("boom"),
        ) as mock_write_snapshot,
        patch("deejay_set_processor.update_deejay_set_collection.log") as mock_log,
        patch(
            "deejay_set_processor.update_deejay_set_collection.config"
        ) as mock_config,
        patch(
            "deejay_set_processor.update_deejay_set_collection.create_collection_snapshot"
        ) as mock_create_snapshot,
    ):
        mock_google_api.from_env.return_value = fake_g
        mock_config.DJ_SETS_FOLDER_ID = "dj-sets-folder"
        mock_config.OUTPUT_NAME = "DJ Set Collection"
        mock_config.TEMP_TAB_NAME = "Temp"
        mock_config.SUMMARY_TAB_NAME = "Summary"
        mock_create_snapshot.return_value = {"folders": []}

        generate_dj_set_collection()

        mock_write_snapshot.assert_called_once()
        mock_log.exception.assert_called()
        drive.find_or_create_spreadsheet.assert_called_once()
        sheets.clear_all_except_one_sheet.assert_called_once()
        sheets.delete_sheet_by_name.assert_any_call("spreadsheet-id", "Temp")
