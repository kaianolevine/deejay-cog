import contextlib
import os
import re
from dataclasses import dataclass, field

import kaiano.config as config
from kaiano import logger as logger_mod
from kaiano.google import GoogleAPI

from deejay_set_processor.ingest_to_api import (
    build_ingest_payload,
    read_tracks_from_sheet,
)
from deejay_set_processor.pipeline_evaluator import evaluate_pipeline_run

log = logger_mod.get_logger()


@dataclass
class CsvPipelineStats:
    """Counters for a process_new_files run (used for AI evaluation)."""

    sets_attempted: int = 0
    sets_imported: int = 0
    sets_failed: int = 0
    sets_skipped_non_csv: int = 0
    skipped_bad_filename: int = 0
    duplicate_csv: int = 0
    total_tracks: int = 0
    failed_set_labels: list[str] = field(default_factory=list)
    ingest_attempted: int = 0
    ingest_failed: int = 0


def normalize_prefixes_in_source(drive) -> None:
    """Remove leading status prefixes from files in the CSV source folder.

    If a file name starts with 'FAILED_', 'possible_duplicate_', or 'Copy of ' (case-insensitive),
    this will attempt to rename it to the stripped base name, but only if that target name does
    not already exist in the source folder.

    This function expects the new Drive facade / DriveFacade interface.
    """

    FAILED_PREFIX = "FAILED_"
    POSSIBLE_DUPLICATE_PREFIX = "possible_duplicate_"
    COPY_OF_PREFIX = "Copy of "

    try:
        log.debug("normalize_prefixes_in_source: listing source folder files")
        files = drive.list_files(
            config.CSV_SOURCE_FOLDER_ID, include_folders=False, trashed=False
        )
        log.info(f"normalize_prefixes_in_source: found {len(files)} files to inspect")

        # Build a quick lookup of names already present in the folder
        existing_names = {f.name for f in files if f.name}

        for f in files:
            original_name = f.name or ""
            lower = original_name.lower()
            prefix = None

            if lower.startswith(FAILED_PREFIX.lower()):
                prefix = original_name[: len(FAILED_PREFIX)]
            elif lower.startswith(POSSIBLE_DUPLICATE_PREFIX.lower()):
                prefix = original_name[: len(POSSIBLE_DUPLICATE_PREFIX)]
            elif lower.startswith(COPY_OF_PREFIX.lower()):
                prefix = original_name[: len(COPY_OF_PREFIX)]

            if not prefix:
                continue

            new_name = original_name[len(prefix) :]
            if not new_name:
                log.warning(
                    f"normalize_prefixes_in_source: derived empty new name for {original_name}, skipping"
                )
                continue

            if new_name in existing_names:
                log.info(
                    f"normalize_prefixes_in_source: target name '{new_name}' already exists in source folder — leaving '{original_name}' as-is"
                )
                continue

            try:
                log.info(
                    f"normalize_prefixes_in_source: renaming '{original_name}' -> '{new_name}'"
                )
                drive.rename_file(f.id, new_name)
                # Keep our local set consistent for subsequent checks in this run
                existing_names.discard(original_name)
                existing_names.add(new_name)
            except Exception as e:
                log.error(
                    f"normalize_prefixes_in_source: failed to rename {original_name}: {e}"
                )

    except Exception as e:
        log.error(f"normalize_prefixes_in_source: unexpected error: {e}")


# --- Utility: remove summary file for a given year ---
def remove_summary_file_for_year(g: GoogleAPI, year: str) -> None:
    try:
        summary_folder_id = g.drive.ensure_folder(config.DJ_SETS_FOLDER_ID, "Summary")
        summary_name = f"{year} Summary"

        # List files in the Summary folder and delete any exact name matches
        files = g.drive.list_files(
            summary_folder_id, include_folders=False, trashed=False
        )
        for f in files:
            if (f.name or "") == summary_name:
                g.drive.delete_file(f.id)
                log.info(
                    f"🗑️ Deleted existing summary file '{summary_name}' for year {year}"
                )
    except Exception as e:
        log.error(f"Failed to remove summary file for year {year}: {e}")


# --- Utility: check for duplicate base filename in a folder ---
def file_exists_with_base_name(g: GoogleAPI, folder_id: str, base_name: str) -> bool:
    try:
        candidates = g.drive.list_files(folder_id, include_folders=False, trashed=False)
        for f in candidates:
            if os.path.splitext(f.name or "")[0] == base_name:
                return True
    except Exception as e:
        log.error(f"Error checking for duplicates in folder {folder_id}: {e}")
    return False


def rename_file_as_duplicate(g: GoogleAPI, file_id: str, filename: str) -> None:
    try:
        new_name = f"possible_duplicate_{filename}"
        g.drive.rename_file(file_id, new_name)
        log.info(f"✏️ Renamed original to '{new_name}'")
    except Exception as rename_exc:
        log.error(f"Failed to rename original to possible_duplicate_: {rename_exc}")


def process_non_csv_file(
    g: GoogleAPI,
    file_metadata: dict,
    year: str,
    stats: CsvPipelineStats | None = None,
) -> None:
    filename = file_metadata["name"]
    file_id = file_metadata["id"]
    log.info(f"\n📄 Moving non-CSV file that starts with year: {filename}")
    try:
        year_folder_id = g.drive.ensure_folder(config.DJ_SETS_FOLDER_ID, year)
        base_name = os.path.splitext(filename)[0]
        if file_exists_with_base_name(g, year_folder_id, base_name):
            rename_file_as_duplicate(g, file_id, filename)
            if stats is not None:
                stats.sets_skipped_non_csv += 1
            return

        g.drive.move_file(
            file_id, new_parent_id=year_folder_id, remove_from_parents=True
        )
        log.info(f"📦 Moved original file to {year} subfolder: {filename}")
        remove_summary_file_for_year(g, year)
        if stats is not None:
            stats.sets_skipped_non_csv += 1
    except Exception as e:
        log.error(f"Failed to move non-CSV file {filename}: {e}")


def process_csv_file(
    g: GoogleAPI,
    file_metadata: dict,
    year: str,
    stats: CsvPipelineStats | None = None,
) -> str:
    """Process one CSV. Returns imported | failed | duplicate."""
    filename = file_metadata["name"]
    file_id = file_metadata["id"]
    log.info(f"\n🚧 Processing: {filename}")
    temp_path = os.path.join("/tmp", filename)

    try:
        g.drive.download_file(file_id, temp_path)
        _normalize_csv(temp_path)
        log.info(f"Downloaded and normalized file: {filename}")

        year_folder_id = g.drive.ensure_folder(config.DJ_SETS_FOLDER_ID, year)
        base_name = os.path.splitext(filename)[0]
        if file_exists_with_base_name(g, year_folder_id, base_name):
            log.warning(
                f"⚠️ Destination already contains file with base name '{base_name}' in year folder {year_folder_id}. Marking original as possible duplicate and skipping."
            )
            rename_file_as_duplicate(g, file_id, filename)
            if stats is not None:
                stats.duplicate_csv += 1
            return "duplicate"

        sheet_id = g.drive.upload_csv_as_google_sheet(
            temp_path, parent_id=year_folder_id
        )
        log.debug(f"Uploaded sheet ID: {sheet_id}")
        g.sheets.formatter.apply_formatting_to_sheet(sheet_id)
        remove_summary_file_for_year(g, year)

        if stats is not None:
            stats.sets_imported += 1
            try:
                tracks = read_tracks_from_sheet(g, sheet_id)
                stats.total_tracks += len(tracks or [])
            except Exception as track_exc:
                log.warning(
                    "Could not read tracks from new sheet for stats: %s", track_exc
                )

        try:
            archive_folder_id = g.drive.ensure_folder(year_folder_id, "Archive")
            g.drive.move_file(
                file_id, new_parent_id=archive_folder_id, remove_from_parents=True
            )
            log.info(f"📦 Moved original file to Archive subfolder: {filename}")

            base_name = os.path.splitext(filename)[0]
            set_date, venue = _extract_date_and_venue(base_name)
            if set_date and venue:
                _ingest_set_to_api(
                    spreadsheet_id=sheet_id,
                    set_date=set_date,
                    venue=venue,
                    label=base_name,
                    g=g,
                    stats=stats,
                )
            else:
                log.warning(
                    "Could not extract date/venue from filename; skipping API ingest for %s",
                    base_name,
                )
        except Exception as move_exc:
            log.error(f"Failed to move original file to Archive subfolder: {move_exc}")

        return "imported"

    except Exception as e:
        log.error(f"❌ Failed to upload or format {filename}: {e}")
        try:
            failed_name = f"FAILED_{filename}"
            g.drive.rename_file(file_id, failed_name)
            log.info(f"✏️ Renamed original to '{failed_name}'")
            if stats is not None:
                stats.sets_failed += 1
                stats.failed_set_labels.append(os.path.splitext(filename)[0])
        except Exception as rename_exc:
            log.error(f"Failed to rename original to FAILED_: {rename_exc}")
        return "failed"
    finally:
        if os.path.exists(temp_path):
            with contextlib.suppress(Exception):
                os.remove(temp_path)


def _extract_year_from_filename(filename: str) -> str | None:
    log.debug(f"extract_year_from_filename called with filename: {filename}")
    match = re.match(r"(\d{4})[-_]", filename)
    year = match.group(1) if match else None
    log.debug(f"Extracted year: {year} from filename: {filename}")
    return year


def _extract_date_and_venue(
    base_name: str,
) -> tuple[str, str] | tuple[None, None]:
    """
    Extract date and venue from a base filename.
    Returns (date_str, venue_str) or (None, None) if no match.
    e.g. "2024-01-15 MADjam" → ("2024-01-15", "MADjam")
    """
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$", base_name.strip())
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _ingest_set_to_api(
    spreadsheet_id: str,
    set_date: str,
    venue: str,
    label: str,
    g: GoogleAPI,
    stats: CsvPipelineStats | None = None,
) -> None:
    """
    Send a single newly processed set to deejay-marvel-api.
    Skips gracefully if KAIANO_API_BASE_URL is not set.
    Logs success or failure but never raises — pipeline must continue.
    """
    import os as _os

    if not _os.environ.get("KAIANO_API_BASE_URL"):
        log.warning("KAIANO_API_BASE_URL not set — skipping API ingest for %s", label)
        return

    try:
        from kaiano.api import KaianoApiClient  # type: ignore
        from kaiano.api.errors import KaianoApiError  # type: ignore
    except Exception as e:
        log.error("❌ API client not available; skipping ingest for %s: %s", label, e)
        return

    try:
        tracks = read_tracks_from_sheet(g, spreadsheet_id)
        payload = build_ingest_payload(
            set_date=set_date,
            venue=venue,
            source_file=label,
            tracks=tracks,
        )
        final_tracks = payload.get("tracks") or []
        if not final_tracks:
            log.warning("⚠️ No tracks to ingest for %s", label)
            return

        if stats is not None:
            stats.ingest_attempted += 1
        client = KaianoApiClient.from_env()
        client.post("/v1/ingest", payload)
        log.info("✅ Ingested to API: %s (%d tracks)", label, len(final_tracks))
    except KaianoApiError as e:
        if stats is not None:
            stats.ingest_failed += 1
        log.error("❌ API ingest failed for %s: %s", label, e)
    except Exception as e:
        if stats is not None and stats.ingest_attempted > 0:
            stats.ingest_failed += 1
        log.error("❌ Unexpected error during API ingest for %s: %s", label, e)


def _normalize_csv(file_path: str) -> None:
    """
    Normalize a CSV file before upload.

    This does the following, in order:
    1) Removes a leading `sep=...` line if present (case-insensitive).
    2) Drops empty / whitespace-only lines.
    3) Normalizes runs of whitespace at a text level.

    NOTE: This does NOT parse CSV structure.
    """
    log.debug(f"normalize_csv called with file_path: {file_path} - reading file")

    with open(file_path) as f:
        lines = f.readlines()

    cleaned_lines: list[str] = []
    for i, line in enumerate(lines):
        # Strip whitespace and any UTF-8 BOM (Excel often writes BOM + sep=,)
        raw = line.strip().lstrip("\ufeff")

        # Drop empty lines
        if not raw:
            continue

        # Drop Excel-style separator hint (e.g. "sep=,") if it appears as the first line
        if i == 0 and raw.lower().startswith("sep="):
            log.info(f"Removed CSV separator hint line: {raw}")
            continue

        cleaned = re.sub(r"\s+", " ", raw)
        cleaned_lines.append(cleaned)

    log.debug(f"Lines after cleaning: {len(cleaned_lines)}")

    with open(file_path, "w") as f:
        f.write("\n".join(cleaned_lines))

    log.debug(f"✅ Normalized: {file_path}")


# === MAIN ===
def main():
    log.info("Starting main process")
    g = GoogleAPI.from_env()

    # Normalize any leftover status prefixes before processing
    normalize_prefixes_in_source(g.drive)

    files = g.drive.list_files(
        config.CSV_SOURCE_FOLDER_ID, include_folders=False, trashed=False
    )
    files = [{"id": f.id, "name": f.name} for f in files]
    log.info(f"Found {len(files)} files in source folder")

    stats = CsvPipelineStats()

    for file_metadata in files:
        filename = file_metadata["name"]
        log.debug(f"Processing file: {filename}")

        year = _extract_year_from_filename(filename)
        if not year:
            log.warning(f"⚠️ Skipping unrecognized filename format: {filename}")
            stats.skipped_bad_filename += 1
            continue

        # If the file is not a CSV but starts with a year, move it straight to the year folder
        if not filename.lower().endswith(".csv"):
            process_non_csv_file(g, file_metadata, year, stats)
            continue

        # At this point we only process CSVs
        stats.sets_attempted += 1
        process_csv_file(g, file_metadata, year, stats)

    log.info(
        "✅ Done: %d CSVs, %d non-CSV files, %d skipped.",
        stats.sets_attempted,
        stats.sets_skipped_non_csv,
        stats.skipped_bad_filename,
    )

    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("KAIANO_API_BASE_URL"):
        run_id = os.environ.get("GITHUB_RUN_ID", "local-run")
        try:
            evaluate_pipeline_run(
                run_id=run_id,
                repo="deejay-set-processor-dev",
                sets_imported=stats.sets_imported,
                sets_failed=stats.sets_failed,
                sets_skipped=stats.sets_skipped_non_csv,
                total_tracks=stats.total_tracks,
                failed_set_labels=list(stats.failed_set_labels),
                api_ingest_success=(stats.ingest_failed == 0),
                sets_attempted=stats.sets_attempted,
                collection_update=False,
                unrecognized_filename_skips=stats.skipped_bad_filename,
                duplicate_csv_count=stats.duplicate_csv,
            )
        except Exception:
            log.exception(
                "Pipeline evaluation raised unexpectedly (should be best-effort)"
            )


if __name__ == "__main__":
    main()
