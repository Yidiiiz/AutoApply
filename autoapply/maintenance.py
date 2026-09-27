"""Explicit operator maintenance; never dispatched by ordinary form resume."""


def refresh_untouched_upload_protocol(engine, app_id):
    """Validate and acknowledge an old repair request without live class swapping.

    New code is loaded by restarting the worker. An existing untouched tracker
    needs no evidence migration; selected/requested uploads must never be reset.
    """
    page = engine.handoff.pages.get(app_id)
    tracker = getattr(page, '_autoapply_uploads', None) if page else None
    if not tracker or tracker.selected or tracker.requests or engine.db.application(app_id)['submit_intent_at']:
        raise ValueError('Upload protocol maintenance requires an untouched same-session tracker')
    engine.db.set_setting(f'refresh_upload_protocol:{app_id}', False)
    engine.db.event(app_id, 'UPLOAD_PROTOCOL_MAINTENANCE', 'Untouched tracker verified; worker restart required for code updates')
