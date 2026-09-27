"""Shared read-only operator projection. Persisted flags never prove live pages."""


def snapshot(db, app_id, *, live_page=None, live_browser=False):
    app = db.application(app_id)
    live = bool(live_browser and live_page is not None and not live_page.is_closed())
    checkpoint = db.setting(f'fill_checkpoint:{app_id}', {})
    return {
        'application_id': app_id, 'lifecycle': app['status'], 'application_state': app['application_state'],
        'listing': {'status': app['listing_status'], 'active': bool(app['listing_active']), 'freshness': app['freshness_state']},
        'security': app['security_state'], 'verification': app['verification_state'],
        'manual_hold': {'required': bool(app['manual_action_required']), 'reason': app['manual_action_reason'],
                        'resume_allowed': bool(app['manual_resume_allowed'])},
        'retry': {'allowed': bool(app['retry_allowed']), 'at': app['retry_at'],
                  'attempts': app['attempts'], 'max_retries': app['max_retries'], 'delivery': app['delivery_state']},
        'submission': {'intent_at': app['submit_intent_at'], 'confirmed': bool(app['submission_confirmation_seen']),
                       'evidence': app['submission_confirmation_reason']},
        'session': {'preserved_flag': bool(app['session_preserved']), 'live_page': live,
                    'live_browser': bool(live_browser), 'availability': 'LIVE' if live else 'UNVERIFIED_OR_UNAVAILABLE'},
        'checkpoint': checkpoint,
        'readiness': ('LIVE_READY_FOR_MANUAL_SUBMIT' if live and app['application_state'] == 'READY_FOR_MANUAL_SUBMIT'
                      else checkpoint.get('readiness', 'NONE')),
    }
