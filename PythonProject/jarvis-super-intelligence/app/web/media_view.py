"""The MEDIA workspace page: upload, preview, and the boundaries stated where they bite.

Three properties this module is built to keep.

**One ingest path.** The upload control calls ``media.store.ingest`` -- the exact
function the HTTP API calls. A file cannot pass one front door and skip the other, and
the page never re-implements a check it could get wrong.

**Server-side re-validation, because NiceGUI's upload route is not an auth route.**
NiceGUI accepts an upload for the connected client id and applies ``max_file_size`` in
the browser. Both are claims. So the handler re-reads the identity from the page's own
session (captured when the page was built, not sent by the client), reads the bytes in
bounded chunks, and lets the probe decide what they are.

**Previews are same-origin and session-authenticated, not credentialed by URL.**
``/media/asset/{id}`` is served on the NiceGUI app, so the session cookie that already
protects every page protects the pixels. A token in a query string would land in access
logs, browser history and Referer headers, so it is not offered as an alternative.
"""
from fastapi import Response as FastAPIResponse
from fastapi.responses import JSONResponse
from nicegui import ui

from ..media import probe, store


def _stamp(value):
    """A stored epoch as a UTC second, or the words that replace a fake timestamp."""
    import time
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(value)))
    except (TypeError, ValueError, OverflowError):
        return "not recorded"


def _size(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    if number >= 1_048_576:
        return "%.2f MiB" % (number / 1_048_576)
    if number >= 1024:
        return "%.1f KiB" % (number / 1024)
    return "%d B" % number


def _duration(value):
    return "%.3f s" % float(value) if value else "not readable without a full index"


def message(result):
    """One honest sentence per ingest outcome, including the two that are not failures.

    A rejected file says which limit or which missing capability stopped it; a duplicate
    says the bytes were already there. Nothing in this function can produce "success"
    for a file that was not stored.
    """
    if not result.get("ok"):
        code = result.get("code", "REJECTED")
        if code == "REJECTED":
            return "NOT STORED -- %s" % result.get("error", "the bytes were not accepted")
        if code == "QUOTA_EXCEEDED":
            return "NOT STORED -- the owner's quota is full: %s" % result.get("error", "")
        if code == "WRITE_FAILED":
            return "NOT STORED -- the media root refused the write: %s" % result.get("error", "")
        return "NOT STORED -- %s" % (result.get("error") or code)
    asset = result["asset"]
    if result.get("duplicate"):
        return "ALREADY STORED as %s (%s %s) -- identical bytes, nothing new written" % (
            asset["id"], asset["container"], _size(asset["bytes"]))
    strip = result.get("strip") or {}
    tail = ""
    if strip.get("removed"):
        tail = "; removed %s" % ", ".join(strip["removed"][:4])
    elif strip.get("retained"):
        tail = "; container metadata retained in the stored file"
    return "STORED %s as %s %s%s%s" % (
        asset["id"], asset["container"], _size(asset["bytes"]),
        "; the uploaded name claimed .%s" % asset["filename"].rsplit(".", 1)[-1] if asset["mismatch"] else "",
        tail)


def verify(asset, reader=store.read):
    """Does the stored file still hash to what the record says?

    Computed on demand from the bytes on disk. A record and a file can disagree --
    someone moved it, the disk refused a write, a restore brought back an older tree --
    and the page should say which, not assume the row.
    """
    import hashlib
    try:
        data = reader(asset["rel_path"])
    except ValueError as exc:
        return "PATH REJECTED -- %s" % exc
    if data is None:
        return "FILE MISSING -- the record exists but no file is at its stored path"
    digest = hashlib.sha256(data).hexdigest()
    if digest == asset["sha256"]:
        return "STORED BYTES VERIFIED -- %s, %s on disk" % (digest[:16], _size(len(data)))
    if digest == asset["source_sha256"]:
        return "STORED BYTES ARE THE UPLOAD -- %s; the record's stored hash is stale" % digest[:16]
    return "HASH MISMATCH -- the file on disk hashes to %s, the record says %s" % (digest[:16], (asset["sha256"] or "nothing")[:16])


def stream(asset_id, username, role, db_module):
    """Bytes for an asset this session may read, or the reason it cannot.

    Called from a route on the NiceGUI app, so ``username``/``role`` come from the
    server-side session. Another actor's asset id gets 404, never 403: an existence
    leak is still a leak when it is dressed as a permission answer.
    """
    asset = db_module.media_get(asset_id)
    if not asset or (asset["owner"] != username and role != "owner"):
        return JSONResponse({"error": "asset not found"}, status_code=404)
    try:
        data = store.read(asset["rel_path"])
    except ValueError as exc:
        return JSONResponse({"error": "the recorded path is outside the media root: %s" % exc}, status_code=400)
    if data is None:
        return JSONResponse({"error": "the record exists but no file is present at its stored path"}, status_code=410)
    return FastAPIResponse(content=data, media_type=asset["media_type"] or "application/octet-stream",
                           headers={"Cache-Control": "private, no-store",
                                    "Content-Disposition": "inline",
                                    "X-JARVIS-Media-Container": asset["container"] or "unknown"})


async def read_bounded(file, max_bytes):
    """The uploaded bytes, or ``None`` when the client sent more than the limit.

    Chunked so an oversized file is refused part-way through instead of after it has
    been spooled to the server's temporary storage and read back in full.
    """
    collected, total = [], 0
    async for block in file.iterate(chunk_size=1 << 20):
        total += len(block)
        if total > max_bytes:
            return None
        collected.append(block)
    return b"".join(collected)


def _upload_handler(db_module, owner, status, refresh):
    """The page's own ingest, with the identity the page was built for."""
    async def handle(event):
        max_bytes = probe.limits()["max_asset_bytes"]
        data = await read_bounded(event.file, max_bytes)
        if data is None:
            status.text = ("NOT STORED -- the upload passed %s bytes (JARVIS_MEDIA_MAX_BYTES); "
                           "reading stopped at the limit" % "{:,}".format(max_bytes))
            return
        if not db_module.media_get.__name__:  # pragma: no cover -- never true, keeps the import honest
            return
        result = store.ingest(owner, event.file.name, data, event.file.content_type, source="workspace")
        status.text = message(result)
        if result["ok"]:
            refresh()
    return handle


def _rejected_handler(status):
    def handle(event):
        status.text = ("NOT STARTED -- the browser refused the file before any byte reached the server "
                       "(size limit enforced client-side is a convenience, not a control; the server "
                       "re-checks every upload that does arrive)")
    return handle


def _fact_rows(asset):
    rows = [("container", asset["container"] or "unclassified"),
            ("served as", asset["media_type"] or "application/octet-stream"),
            ("stored", _size(asset["bytes"]))]
    if asset["kind"] == "image":
        rows.append(("pixels", "%s × %s" % (asset["width"], asset["height"])))
        rows.append(("mode", "%s%s" % (asset["pixel_mode"], ", alpha" if asset["has_alpha"] else "")))
        if (asset["frames"] or 0) > 1:
            rows.append(("frames", "%d in the file" % asset["frames"]))
    else:
        rows.append(("duration", _duration(asset["duration_s"])))
        if asset["width"]:
            rows.append(("frame size", "%s × %s" % (asset["width"], asset["height"])))
        rows.append(("tracks", str(asset["tracks"] if asset["tracks"] is not None else "not readable")))
        rows.append(("samples", str(asset["samples"] if asset["samples"] is not None else "not readable")))
    rows.append(("metadata", _metadata_line(asset)))
    rows.append(("stored", _stamp(asset["created_at"])))
    return rows


def _metadata_line(asset):
    if asset["metadata_removed"]:
        return "stripped on write: %s" % ", ".join(asset["metadata_removed"][:4])
    if asset["exif_present"] or asset["gps_present"]:
        return ("retained in the file (%s)" % "camera EXIF, GPS location present"
                if asset["gps_present"] else "camera EXIF present")
    return "none recorded by the container"


def _card(db_module, asset, ident, refresh):
    with ui.column().classes("jmedia-card"):
        with ui.column().classes("jmedia-frame"):
            if asset["kind"] == "image":
                ui.image("/media/asset/%s" % asset["id"]).classes("w-full h-full object-contain")
            else:
                ui.video("/media/asset/%s" % asset["id"]).classes("w-full h-full")
        ui.label(asset["filename"] or asset["id"]).classes("jmedia-name")
        with ui.column().classes("jmedia-facts"):
            for label, value in _fact_rows(asset):
                with ui.row().classes("jmedia-fact"):
                    ui.label(label.upper())
                    ui.label(str(value))
        if asset["mismatch"]:
            ui.label("The uploaded name claimed a different container than the bytes hold. "
                     "The stored file and the served media type follow the bytes.").classes("jfeed-honesty")
        if asset["gps_present"]:
            ui.label("A location was present in this file's metadata. Coordinates are never read into "
                     "this page; where removal is lossless the segments were dropped before storage, "
                     "and where it was not, they remain only in your own stored file.").classes("jfeed-honesty")
        with ui.column().classes("jmedia-note"):
            for note in asset["unreadable"][:3]:
                ui.label("· " + str(note))
        outcome = ui.label("").classes("jmedia-note")
        with ui.row().classes("jmedia-actions"):
            ui.button("Verify stored bytes", color=None, on_click=lambda row=asset, target=outcome: _show(
                target, verify(row))).classes("jbtn jbtn-ghost jbtn-xs").props(
                'aria-label="Hash the file on disk and compare it with the record"')
            ui.button("Ask a model to describe it", color=None, on_click=lambda row=asset, target=outcome: _describe(
                row, target)).classes("jbtn jbtn-ghost jbtn-xs").props(
                'aria-label="Request an AI description of this asset"')
            ui.button("Remove", color=None, on_click=lambda row=asset, box=None: _confirm(
                db_module, row, ident, refresh)).classes("jbtn jbtn-danger jbtn-xs").props(
                'aria-label="Delete this asset and its stored file"')


def _show(target, text):
    target.text = text


def _describe(asset, target):
    from ..intelligence import vision
    answer = vision.describe(asset)
    target.text = "%s -- %s" % (answer["state"], answer["reason"])
    if answer.get("text"):
        target.text = answer["text"]


def _confirm(db_module, asset, ident, refresh):
    """Two-step delete. The row and the file are removed only after a second explicit
    click that repeats the filename and the size being freed."""
    with ui.dialog() as dialog, ui.card().classes("jglass jpanel"):
        with ui.column().classes("gap-2"):
            ui.label("Remove %s?" % (asset["filename"] or asset["id"])).classes("jsection-title")
            ui.label("This deletes the record and the stored file (%s). It is this actor's own asset; "
                     "no other copy is kept." % _size(asset["bytes"])).classes("jmeta")
            outcome = ui.label("").classes("jfeed-honesty")
            with ui.row().classes("jmedia-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("jbtn jbtn-ghost jbtn-xs")

                def remove():
                    removed = db_module.media_delete(asset["id"], ident["username"], ident["role"] == "owner")
                    if removed is None:
                        outcome.text = "The record was already gone; nothing was deleted."
                        return
                    cleanup = store.remove(asset["rel_path"])
                    outcome.text = ("FILE REMOVED" if cleanup["ok"] else
                                    "RECORD REMOVED, FILE LEFT ON DISK: %s -- %s" % (cleanup["code"], cleanup["detail"]))
                    dialog.close()
                    refresh()

                ui.button("Remove it", color=None, on_click=remove).classes("jbtn jbtn-danger jbtn-xs").props(
                    'aria-label="Confirm deletion of this asset and its stored file")'.replace(")", ""))
    dialog.open()


def _assets_region(db_module, ident):
    holder = ui.column().classes("w-full gap-3")

    def refresh():
        holder.clear()
        rows = db_module.media_list(ident["username"], ident["role"] == "owner", 60)
        with holder:
            if not rows:
                ui.label("Nothing is stored for this actor yet. The grid below appears only after a "
                         "file has passed the byte-level probe -- there is no sample asset to look at "
                         "meanwhile.").classes("jempty-sector")
                return
            with ui.row().classes("jmedia-grid"):
                for asset in rows:
                    _card(db_module, asset, ident, refresh)
        count.text = "%d ASSET(S) LISTED" % len(rows)

    count = ui.label("").classes("jlabel")
    refresh()
    return refresh


def _upload_panel(db_module, ident):
    limit = probe.limits()
    with ui.column().classes("jglass jpanel j-reveal j-delay-1"):
        with ui.row().classes("jphead"):
            ui.label("Add media").classes("jlabel")
            ui.space()
            ui.label("%s PER FILE" % _size(limit["max_asset_bytes"])).classes("jlabel")
        with ui.column().classes("jmedia-drop"):
            status = ui.label("No upload has been attempted in this page view.").classes("jmeta")
            status.set_visibility(True)
            with ui.row().classes("jmedia-actions"):
                ui.upload(on_upload=_upload_handler(db_module, ident["username"], status, lambda: None),
                          on_rejected=_rejected_handler(status), auto_upload=True, multiple=True,
                          max_file_size=limit["max_asset_bytes"], max_total_size=limit["max_asset_bytes"],
                          max_files=4, label="Choose images or video").classes("w-full").props(
                          'accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/tiff,'
                          'video/mp4,video/quicktime,video/webm,video/x-matroska"')
        ui.label("Accepted now: %s." % ", ".join(sorted(probe.ACCEPTED_CONTAINERS))).classes("jmeta")
        for line in (
                "The container is decided by the bytes, not by the filename or the browser's "
                "content type. A file whose leading bytes match nothing is refused before it is written.",
                "SVG and other markup are refused on purpose: they can carry script, and stored media "
                "is served inline to this same origin.",
                "JPEG and PNG metadata is removed where the removal is lossless. GIF, WebP, TIFF and "
                "every video keep their bytes untouched, and their cards say so.",
                "Images larger than %s pixels are refused; the limit is JARVIS_MEDIA_MAX_PIXELS."
                % "{:,}".format(limit["max_pixels"]),
        ):
            ui.label(line).classes("jmedia-note")


def _availability_panel():
    report = store.diagnostics()
    description = report["capability"]
    with ui.column().classes("jglass jpanel j-reveal j-delay-2"):
        with ui.row().classes("jphead"):
            ui.label("What this build can actually do with media").classes("jlabel")
        with ui.column().classes("jmedia-facts"):
            for name, state in sorted(description.items()):
                with ui.row().classes("jmedia-fact"):
                    ui.label(name.replace("_", " ").upper())
                    ui.label(state)
        notes = report["capability_notes"]
        for name in sorted(notes):
            ui.label("%s: %s" % (name.replace("_", " "), notes[name])).classes("jmedia-note")
        ui.label("Pillow %s · ffmpeg %s" % (report["pillow"]["version"] or "NOT_INSTALLED",
                                            report["ffmpeg"]["state"])).classes("jmeta")
        ui.label("Limits are configuration, not settings: JARVIS_MEDIA_MAX_BYTES %s, "
                 "JARVIS_MEDIA_MAX_PIXELS %s, JARVIS_MEDIA_OWNER_QUOTA_BYTES %s per actor."
                 % ("{:,}".format(report["limits"]["max_asset_bytes"]),
                    "{:,}".format(report["limits"]["max_pixels"]),
                    "{:,}".format(report["limits"]["owner_quota_bytes"]))).classes("jmeta")
        for note in report["limits"]["notes"]:
            ui.label("Configuration problem: " + note).classes("jfeed-honesty")
        disk, records = report["disk"], report["records"]
        ui.label("%d asset record(s); %s of files on disk; %s in %d unfinished .part file(s)."
                 % (records["assets"], _size(disk["bytes"]), _size(disk["partial_bytes"]),
                    disk["partial_files"])).classes("jmeta")
        ui.label("The media root is a directory of this process's own data tree: %s"
                 % report["media_root"]["path"]).classes("jmeta")


def _vision_panel():
    from ..intelligence import vision
    answer = vision.availability()
    with ui.column().classes("jglass jpanel j-reveal j-delay-3"):
        with ui.row().classes("jphead"):
            ui.label("AI description of media").classes("jlabel")
            ui.space()
            ui.label(answer["state"]).classes("jpill jpill--" + ("ok" if answer["state"] == "AVAILABLE" else "warn"))
        ui.label(answer["reason"]).classes("jpanel-lead")
        ui.label("Transport: " + answer["transport"]["detail"]).classes("jmedia-note")
        if answer["rows"]:
            with ui.column().classes("jmedia-facts"):
                for row in answer["rows"]:
                    with ui.row().classes("jmedia-fact"):
                        ui.label("%s / %s" % (row["provider"], row["state"]))
                        ui.label("declares image input: %s · %s" % (
                            "yes" if row["declares_image"] else "no", row["input_modalities"]))
        else:
            ui.label("No provider is registered in this process, so there is nothing to declare an "
                     "input modality.").classes("jempty-sector")
        ui.label("A description is only ever shown when a provider actually returned one. Until then the "
                 "facts on each card are computed locally and labelled as such.").classes("jfeed-honesty")
